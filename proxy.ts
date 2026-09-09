import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Constant-time string comparison — avoids leaking password length/prefix via
// response-timing differences. Avoids Node's Buffer/crypto since middleware
// may run on the Edge runtime, which only supports Web-standard APIs.
function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

const UNAUTHORIZED = new NextResponse("Authentication required", {
  status: 401,
  headers: { "WWW-Authenticate": 'Basic realm="CarHunt"' },
});

export function proxy(req: NextRequest) {
  const user = process.env.DASHBOARD_USERNAME;
  const pass = process.env.DASHBOARD_PASSWORD;

  if (!user || !pass) {
    // Fail closed rather than silently serving the app unauthenticated.
    return new NextResponse(
      "Dashboard auth not configured — set DASHBOARD_USERNAME and DASHBOARD_PASSWORD in .env.local.",
      { status: 500 }
    );
  }

  const authHeader = req.headers.get("authorization") || "";
  const [scheme, encoded] = authHeader.split(" ");
  if (scheme === "Basic" && encoded) {
    let decoded = "";
    try {
      decoded = atob(encoded);
    } catch {
      return UNAUTHORIZED;
    }
    const sep = decoded.indexOf(":");
    const reqUser = sep === -1 ? decoded : decoded.slice(0, sep);
    const reqPass = sep === -1 ? "" : decoded.slice(sep + 1);
    if (timingSafeEqual(reqUser, user) && timingSafeEqual(reqPass, pass)) {
      return NextResponse.next();
    }
  }

  return UNAUTHORIZED;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
