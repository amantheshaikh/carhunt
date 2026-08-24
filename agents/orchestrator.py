"""
Orchestrator — sequences Sourcing → Analysis agents and writes to refresh_log.
Triggered on demand via the dashboard "Refresh Now" button or by running directly.
"""
import concurrent.futures
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from dotenv import load_dotenv

TIMEOUT_SECONDS          = 3600  # 60 min total watchdog (CarGurus scrapes 9 models sequentially)
_TRAINING_TIMEOUT        = 300   # 5 min hard cap on price model training
_SCORING_TIMEOUT         = 300   # 5 min hard cap on analysis/scoring

# Force unbuffered stdout so output appears in logs even when piped
sys.stdout.reconfigure(line_buffering=True)

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env.local"))

from tools.log_tee import enable_log_tee
enable_log_tee()  # tee all output to /tmp/carhunt-refresh.log for the dashboard SSE stream

from tools.db import execute_returning, execute, refresh_self_computed_medians
from tools.job_tracker import start_job, update_job, complete_job
import sourcing_agent
import analysis_agent


def _generate_summary(source_results: dict, source_errors: dict, scored: int, duration_s: float) -> str | None:
    """Call OpenRouter to produce a 2-3 sentence plain-English refresh summary."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("  Summary generation skipped: OPENROUTER_API_KEY is not configured.", flush=True)
        return None
    try:
        from openai import OpenAI
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )
        sources_text = ", ".join(
            f"{src} ({n} listings)" for src, n in sorted(source_results.items())
        )
        errors_text = (
            ", ".join(k for k, v in source_errors.items() if v)
            if source_errors else "none"
        )
        prompt = (
            f"Summarize this car listing scraper refresh in 2-3 plain-English sentences. "
            f"Be direct and specific. Note anything unusual (zero results from a normally active source, errors).\n\n"
            f"Duration: {duration_s:.0f}s\n"
            f"Sources scraped: {sources_text}\n"
            f"New listings scored: {scored}\n"
            f"Errors: {errors_text}"
        )
        resp = client.chat.completions.create(
            model="deepseek/deepseek-v4-flash",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=80,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"  Summary generation failed: {e}", flush=True)
        return None


watchdog_event = threading.Event()


def _start_watchdog(run_id: str) -> threading.Thread:
    """Daemon thread that force-kills the process after TIMEOUT_SECONDS.
    signal.SIGALRM is unreliable with asyncio on macOS — this always fires."""
    def _kill():
        if watchdog_event.wait(TIMEOUT_SECONDS):
            return  # Event set; refresh completed successfully
        print(f"[TIMEOUT] Refresh exceeded {TIMEOUT_SECONDS}s — force terminating", file=sys.stderr, flush=True)
        try:
            execute(
                "UPDATE refresh_log SET completed_at = now(), source_errors = %s WHERE id = %s AND completed_at IS NULL",
                (json.dumps({"error": "timeout"}), run_id),
            )
        except Exception:
            pass
        os.kill(os.getpid(), signal.SIGTERM)
    t = threading.Thread(target=_kill, daemon=True, name="watchdog")
    t.start()
    return t


def _run_phase(fn, timeout_s: int, label: str):
    """Run a synchronous callable with a hard timeout.
    Returns the result on success, None on timeout or any error (thread is abandoned)."""
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    fut = executor.submit(fn)
    try:
        return fut.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError:
        print(f"  [{label}] timed out after {timeout_s}s — skipping", flush=True)
        return None
    except Exception as e:
        import traceback
        print(f"  [{label}] failed: {e}", flush=True)
        traceback.print_exc()
        return None
    finally:
        executor.shutdown(wait=False)


def _kill_stale_browsers() -> None:
    """Kill any orphaned Playwright/Chromium processes left by previously killed runs."""
    subprocess.run(
        ["pkill", "-f", "chrome-headless-shell|playwright/driver/node"],
        capture_output=True,
    )


def run(force: bool = False):
    _kill_stale_browsers()
    start_time = time.time()
    print(f"[{datetime.now().isoformat()}] Starting refresh...", flush=True)

    # Open a run log entry
    log = execute_returning(
        "INSERT INTO refresh_log (started_at) VALUES (now()) RETURNING id",
    )
    run_id = str(log["id"])
    
    # Reset watchdog event and start watchdog
    watchdog_event.clear()
    _start_watchdog(run_id)

    job_id = start_job("refresh", stage_total=4)
    try:
        # Phase 1: Sourcing
        print("Running Sourcing Agent...", flush=True)
        update_job(job_id, stage_current=1, stage_label="Sourcing listings")
        t0 = time.time()
        sourcing_result = sourcing_agent.run(run_id, force=force)
        print(f"Sourcing complete in {time.time()-t0:.0f}s", flush=True)
        source_results = sourcing_result.get("source_results", {})
        source_errors = sourcing_result.get("source_errors", {})
        total_found = sum(source_results.values())
        print(f"  {total_found} listings found across {len(source_results)} sources")

        # Phase 1b: Refresh market price cache from scraped data
        print("Refreshing market price cache...", flush=True)
        update_job(job_id, stage_current=2, stage_label="Refreshing price cache")
        t0 = time.time()
        cached = refresh_self_computed_medians()
        print(f"  {cached} cache rows updated in {time.time()-t0:.0f}s", flush=True)

        # Phase 1c: Retrain price model with freshest data
        print("Retraining price model...", flush=True)
        update_job(job_id, stage_current=3, stage_label="Retraining price model")
        t0 = time.time()
        try:
            from tools.price_model import train as train_price_model
            result = _run_phase(train_price_model, _TRAINING_TIMEOUT, "price_model")
            if result is not None:
                print(f"  Price model trained in {time.time()-t0:.0f}s", flush=True)
        except Exception as e:
            print(f"  Price model skipped: {e}", flush=True)

        # Phase 2: Analysis
        print("Running Analysis Agent...", flush=True)
        update_job(job_id, stage_current=4, stage_label="Scoring listings")
        t0 = time.time()
        analysis_result = _run_phase(analysis_agent.run, _SCORING_TIMEOUT, "scoring") or {}
        scored = analysis_result.get("scored", 0)
        print(f"Analysis complete: {scored} listings scored in {time.time()-t0:.0f}s", flush=True)

        # Generate LLM summary
        update_job(job_id, stage_label="Generating summary")
        summary = _generate_summary(source_results, source_errors, scored, time.time() - start_time)
        if summary:
            print(f"  Summary: {summary}", flush=True)

        # Close run log
        execute(
            """UPDATE refresh_log SET
               completed_at = now(),
               listings_found = %s,
               listings_new = %s,
               source_results = %s,
               source_errors = %s,
               summary = %s
               WHERE id = %s""",
            (
                total_found,
                scored,
                json.dumps(source_results),
                json.dumps(source_errors) if source_errors else None,
                summary,
                run_id,
            ),
        )
        complete_job(job_id)
        print(f"[{datetime.now().isoformat()}] Refresh complete.", flush=True)
        return {"ok": True, "listings_found": total_found, "scored": scored}

    except Exception as e:
        execute(
            "UPDATE refresh_log SET completed_at = now(), source_errors = %s WHERE id = %s",
            (json.dumps({"orchestrator": str(e)}), run_id),
        )
        complete_job(job_id, status="error")
        print(f"ERROR: {e}", file=sys.stderr)
        raise
    finally:
        # Signal watchdog thread to exit cleanly and kill any stale browser instances
        watchdog_event.set()
        _kill_stale_browsers()


if __name__ == "__main__":
    force = "--force" in sys.argv
    run(force=force)
