import { NextResponse } from "next/server";
import { spawn } from "child_process";
import fs from "fs";

export async function POST() {
  const agentsDir = process.env.AGENTS_DIR ?? "agents";
  const venvPython = process.env.AGENTS_PYTHON ?? "python3";

  if (!fs.existsSync(venvPython)) {
    return NextResponse.json(
      { error: "Python venv not found. Run: cd agents && python3 -m venv .venv && .venv/bin/pip install -r ../requirements.txt" },
      { status: 500 }
    );
  }

  const logFile = fs.openSync("/tmp/carhunt-refresh.log", "a");

  const child = spawn(venvPython, ["-u", "orchestrator.py"], {
    cwd: agentsDir,
    detached: true,
    stdio: ["ignore", logFile, logFile],
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
  });
  child.unref();

  return NextResponse.json({ ok: true });
}
