import { NextResponse } from "next/server";

function resolveBackendBase(): string {
  const env = process.env.BACKEND_API_URL?.trim();
  if (env) return env.replace(/\/$/, "");
  return "http://127.0.0.1:8000";
}

export async function POST(req: Request) {
  try {
    const payload = await req.json();
    const res = await fetch(`${resolveBackendBase()}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      cache: "no-store",
    });

    const bodyText = await res.text();
    return new NextResponse(bodyText, {
      status: res.status,
      headers: { "Content-Type": res.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json(
      { error: "Could not reach backend chat service on :8000" },
      { status: 502 },
    );
  }
}
