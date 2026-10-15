import { cookies } from "next/headers";

const API_URL = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
const COOKIE = "oae_admin_token";

async function forward(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const token = (await cookies()).get(COOKIE)?.value;
  if (!token) return Response.json({ detail: "Sign in with the server administrator key." }, { status: 401 });
  const { path } = await context.params;
  const incoming = new URL(request.url);
  const destination = `${API_URL}/${path.map(encodeURIComponent).join("/")}${incoming.search}`;
  const headers = new Headers(request.headers);
  headers.delete("cookie");
  headers.delete("host");
  headers.set("authorization", `Bearer ${token}`);
  const upstream = await fetch(destination, {
    method: request.method,
    headers,
    body: ["GET", "HEAD"].includes(request.method) ? undefined : await request.arrayBuffer(),
    cache: "no-store",
  });
  const responseHeaders = new Headers(upstream.headers);
  responseHeaders.delete("content-encoding");
  responseHeaders.delete("transfer-encoding");
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export const GET = forward;
export const POST = forward;
export const PATCH = forward;
export const DELETE = forward;
