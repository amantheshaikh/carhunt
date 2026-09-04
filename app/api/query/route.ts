import { NextRequest, NextResponse } from "next/server";
import { execFile } from "child_process";
import fs from "fs";

export async function POST(req: NextRequest) {
  const { question, history } = await req.json();
  if (!question?.trim() && (!history || history.length === 0)) {
    return NextResponse.json({ error: "No question or history provided" }, { status: 400 });
  }

  const agentsDir = process.env.AGENTS_DIR ?? "agents";
  const venvPython = process.env.AGENTS_PYTHON ?? "python3";

  if (!fs.existsSync(venvPython)) {
    return NextResponse.json(
      { error: "Python venv not found. Run: cd agents && python3 -m venv .venv && .venv/bin/pip install -r ../requirements.txt" },
      { status: 500 }
    );
  }

  const payload = Array.isArray(history) && history.length > 0
    ? history
    : [{ role: "user", content: question }];

  return new Promise<NextResponse>((resolve) => {
    const child = execFile(
      venvPython,
      ["query_agent.py"],
      { cwd: agentsDir, timeout: 60_000 },
      (err, stdout, stderr) => {
        if (err) {
          console.error("[query_agent] error:", stderr);
          resolve(NextResponse.json({ error: "Query failed" }, { status: 500 }));
          return;
        }
        try {
          const result = JSON.parse(stdout.trim());
          resolve(NextResponse.json(result));
        } catch {
          resolve(NextResponse.json({ error: "Invalid response from agent", raw: stdout }, { status: 500 }));
        }
      }
    );

    child.stdin?.write(JSON.stringify(payload));
    child.stdin?.end();
  });
}
