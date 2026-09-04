"""
Query Agent — answers natural language questions about listings in the database.

Uses Text-to-SQL: the LLM generates a SELECT query, runs it via the query_db tool,
then returns a plain-English answer. No vector embeddings needed — the data is
fully structured and SQL is the right retrieval mechanism.

Called by the Next.js API route with the question passed as a CLI argument.
Prints a single JSON object to stdout: {answer, sql, rows}.
"""
import json
import os
import re
import sys
import time
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env.local"))

from openai import OpenAI
from tools.db import fetch_all

MODEL = "deepseek/deepseek-v4-flash"

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPENROUTER_API_KEY"],
)

SCHEMA = """
Database schema (PostgreSQL):

listings
  id uuid PK
  vin text              -- 17-char vehicle identification number, may be null
  source text          -- 'cargurus' | 'carscom' | 'dealer'
  condition text       -- 'new' | 'used'
  make text, model text, trim text, year int
  body_type text       -- 'sedan' | 'suv' | 'crossover'
  mileage int, price int
  dealer_name text, dealer_phone text, listing_url text
  status text          -- 'new'|'active'|'dismissed'
  title_status text    -- 'clean'|'salvage'|'rebuilt'|'flood'|'lemon'|'unknown'
  accident_count int
  is_pinned bool
  first_seen_at timestamptz, last_seen_at timestamptz
  insights jsonb       -- JSON data containing:
                       --   "cpo": boolean (is it Certified Pre-Owned?)
                       --   "packages": text[] (array of option packages, e.g. ["Premium Package"]. Names vary in casing/phrasing, so always do case-insensitive substring searches using EXISTS and ILIKE)
                       --   "price_drop": int or null (dollar drop in price)
                       --   "owner_notes": text or null (notable notes)

listing_scores
  listing_id uuid FK → listings.id
  value_score numeric       -- 0-100; pure price deviation from ML fair value
  fair_price          int   -- ML-predicted fair price (encodes year/mileage/trim/drivetrain/powertrain)
  market_price_source text  -- 'ml_model'

Common query patterns:
  - Query listings with a specific package (e.g. 'technology'), joining scores and fetching packages:
    SELECT l.make, l.model, l.year, l.price, l.mileage, ls.value_score, l.insights -> 'packages' AS packages
    FROM listings l
    LEFT JOIN listing_scores ls ON ls.listing_id = l.id
    WHERE l.status = 'active'
      AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(l.insights -> 'packages') p WHERE p ILIKE '%technology%')
    ORDER BY l.price ASC
    LIMIT 20;

  - Query listings that are Certified Pre-Owned (CPO):
    SELECT l.make, l.model, l.year, l.price, l.mileage, ls.value_score
    FROM listings l
    LEFT JOIN listing_scores ls ON ls.listing_id = l.id
    WHERE l.status = 'active'
      AND (l.insights ->> 'cpo')::boolean = true
    ORDER BY l.price ASC
    LIMIT 20;
"""

SYSTEM = f"""You are a car-deal assistant for a personal car shopping tracker called CarHunt.
You have read-only access to the user's listings database via the query_db tool.

{SCHEMA}

Rules:
- Only run SELECT queries. Never UPDATE, INSERT, DELETE, or DROP.
- Limit results to 20 rows unless the user explicitly asks for more.
- When listing cars, always include make, model, year, price, mileage, and value_score.
- When filtering by option packages (in the `insights -> 'packages'` array), ALWAYS use case-insensitive substring matching (via the `EXISTS` and `jsonb_array_elements_text` pattern with `ILIKE`, e.g. `%technology%`) rather than containment (`@>`) or exact checks (`?`), because package names vary in casing and phrasing (e.g. "CONVENIENCE PACKAGE" vs "Convenience Package" vs "SEL Convenience").
- Be concise. Answer in 1-3 sentences max, then list results if relevant.
- If a query returns no rows, say so plainly.
- Use plain dollars (e.g. "$24,500") not raw numbers when talking about prices.
- query_db returns {{"rows": [...], "row_count": N, "truncated": bool}}. If truncated
  is true, tell the user you're showing a partial result (e.g. "showing 20 of N") —
  add a LIMIT/ORDER BY to your SQL to control which rows they see rather than relying
  on the automatic cutoff.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_db",
            "description": "Run a read-only SELECT query against the CarHunt listings database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "A valid PostgreSQL SELECT statement.",
                    }
                },
                "required": ["sql"],
            },
        },
    }
]

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


_ROW_CAP = 20


def _query_db(sql: str) -> str:
    sql = sql.strip()
    print(f"Executing SQL: {sql}", file=sys.stderr)
    if not sql.upper().startswith("SELECT"):
        return json.dumps({"error": "Only SELECT queries are permitted."})
    if _FORBIDDEN.search(sql):
        return json.dumps({"error": "Query contains forbidden keywords."})
    try:
        # params=None (not the default ()) tells psycopg2 to send the SQL as-is with
        # no %-style mogrification, so literal '%' in the query (e.g. ILIKE '%foo%')
        # doesn't need manual escaping and can't be mangled by a blanket replace.
        rows = fetch_all(sql, params=None)
        total = len(rows)
        return json.dumps({
            "rows": [dict(r) for r in rows[:_ROW_CAP]],
            "row_count": total,
            "truncated": total > _ROW_CAP,
        }, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


def _assistant_msg(msg) -> dict:
    d: dict = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        d["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in msg.tool_calls
        ]
    return d


_VALID_ROLES = {"user", "assistant"}
# Leaves buffer under the Node route's 60s execFile timeout so this function always
# returns its own graceful "reached the limit" message rather than getting killed
# mid-loop by the outer process timeout.
_TIME_BUDGET_SECONDS = 45


def run(question_or_history) -> dict:
    messages = [
        {"role": "system", "content": SYSTEM},
    ]
    if isinstance(question_or_history, list):
        for msg in question_or_history:
            # Restrict to user/assistant so a caller can't smuggle an extra
            # "system"-role message into the conversation via the history payload.
            if msg.get("content") and msg.get("role") in _VALID_ROLES:
                messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })
    else:
        messages.append({
            "role": "user",
            "content": str(question_or_history)
        })

    all_sql: list[str] = []
    all_rows: list = []

    start_time = time.time()
    loop_count = 0
    max_loops = 5
    while loop_count < max_loops and (time.time() - start_time) < _TIME_BUDGET_SECONDS:
        loop_count += 1
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            max_tokens=500,
        )
        msg = response.choices[0].message
        print(f"Assistant content: {msg.content}", file=sys.stderr)
        print(f"Assistant tool calls: {msg.tool_calls}", file=sys.stderr)
        messages.append(_assistant_msg(msg))

        if response.choices[0].finish_reason != "tool_calls" or not msg.tool_calls:
            return {
                "answer": msg.content or "",
                "sql": all_sql,
                "rows": all_rows,
            }

        for tc in msg.tool_calls:
            inputs = json.loads(tc.function.arguments)
            result = _query_db(inputs["sql"])
            print(f"Tool result: {result}", file=sys.stderr)
            all_sql.append(inputs["sql"])
            try:
                parsed = json.loads(result)
                if isinstance(parsed, dict) and "rows" in parsed:
                    all_rows.extend(parsed["rows"])
            except Exception:
                pass
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    return {
        "answer": "I reached the maximum limit of database queries while trying to answer your question.",
        "sql": all_sql,
        "rows": all_rows,
    }


if __name__ == "__main__":
    # Check if we have piped input on stdin
    if not sys.stdin.isatty():
        try:
            input_data = sys.stdin.read().strip()
            if input_data:
                payload = json.loads(input_data)
                result = run(payload)
                print(json.dumps(result, default=str))
                sys.exit(0)
        except Exception as e:
            print(json.dumps({"error": f"Failed to parse stdin JSON: {str(e)}"}))
            sys.exit(1)

    # Fallback to command-line arguments
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No question provided via CLI arguments or stdin"}))
        sys.exit(1)
    question = " ".join(sys.argv[1:])
    result = run(question)
    print(json.dumps(result, default=str))
