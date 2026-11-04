import { NextResponse } from "next/server";
import { spawn } from "child_process";
import fs from "fs";
import { getAgentsConfig, isPythonAvailable } from "@/lib/agents";

const LOCK_FILE = "/tmp/carhunt-refresh.pid";

function isRunning(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

export async function POST() {
  const { agentsDir, venvPython } = getAgentsConfig();

  if (!isPythonAvailable(venvPython)) {
    return NextResponse.json(
      { error: "Python venv not found. Run: cd agents && python3 -m venv .venv && .venv/bin/pip install -r ../requirements.txt" },
      { status: 500 }
    );
  }

  // Prevent concurrent refreshes
  if (fs.existsSync(LOCK_FILE)) {
    const pid = parseInt(fs.readFileSync(LOCK_FILE, "utf8").trim(), 10);
    if (!isNaN(pid) && isRunning(pid)) {
      return NextResponse.json({ error: "Refresh already in progress" }, { status: 409 });
    }
    fs.unlinkSync(LOCK_FILE); // stale lock from a crashed run
  }

  const logFile = fs.openSync("/tmp/carhunt-refresh.log", "a");

  const child = spawn(venvPython, ["-u", "orchestrator.py"], {
    cwd: agentsDir,
    detached: true,
    stdio: ["ignore", logFile, logFile],
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
  });
  fs.closeSync(logFile); // child inherited the fd; parent's copy must be closed to avoid leak

  fs.writeFileSync(LOCK_FILE, String(child.pid));
  child.once("close", () => { try { fs.unlinkSync(LOCK_FILE); } catch {} });
  child.unref();

  return NextResponse.json({ ok: true });
}
