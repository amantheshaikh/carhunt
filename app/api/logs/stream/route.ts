import fs from "fs";

const LOG_PATH = "/tmp/carhunt-refresh.log";
const ANSI_RE = /\x1b\[[0-9;]*[\x40-\x7e]/g;

function stripAnsi(s: string): string {
  return s.replace(ANSI_RE, "");
}

export async function GET(req: Request) {
  const encoder = new TextEncoder();

  // Start from current end-of-file so we only stream the current job's output,
  // not the entire history of all previous runs in the log file.
  let position = 0;
  try {
    position = fs.statSync(LOG_PATH).size;
  } catch {
    // File doesn't exist yet — job hasn't started writing; start at 0.
  }

  const stream = new ReadableStream({
    start(controller) {
      const send = (line: string) => {
        try {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(line)}\n\n`));
        } catch {
          // controller already closed (client disconnected)
        }
      };

      const tick = () => {
        try {
          const stat = fs.statSync(LOG_PATH);
          if (stat.size <= position) return;

          const fd = fs.openSync(LOG_PATH, "r");
          const buf = Buffer.alloc(stat.size - position);
          fs.readSync(fd, buf, 0, buf.length, position);
          fs.closeSync(fd);
          position = stat.size;

          for (const raw of buf.toString("utf8").split("\n")) {
            const line = stripAnsi(raw.trimEnd());
            if (line) send(line);
          }
        } catch {
          // file disappeared mid-read — ignore, keep polling
        }
      };

      const interval = setInterval(tick, 500);

      req.signal.addEventListener("abort", () => {
        clearInterval(interval);
        try { controller.close(); } catch {}
      });
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      "Connection": "keep-alive",
    },
  });
}
