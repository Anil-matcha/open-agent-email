import { cookies } from "next/headers";

const API_URL = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
const COOKIE = "oae_admin_token";

export async function GET() {
  const token = (await cookies()).get(COOKIE)?.value;
  if (!token) return Response.json({ authenticated: false }, { status: 401 });
  const check = await fetch(`${API_URL}/v1/stats`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!check.ok) return Response.json({ authenticated: false }, { status: 401 });
  return Response.json({ authenticated: true });
}

export async function POST(request: Request) {
  const { token } = await request.json();
  if (typeof token !== "string" || !token.trim()) {
    return Response.json({ error: "Enter the ADMIN_API_KEY configured on the server." }, { status: 400 });
  }
  const check = await fetch(`${API_URL}/v1/stats`, {
    headers: { Authorization: `Bearer ${token.trim()}` },
    cache: "no-store",
  });
  if (!check.ok) return Response.json({ error: "The server did not accept that administrator key." }, { status: 401 });
  const response = Response.json({ authenticated: true });
  (await cookies()).set(COOKIE, token.trim(), {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production",
    path: "/",
  });
  return response;
}

export async function DELETE() {
  (await cookies()).delete(COOKIE);
  return Response.json({ authenticated: false });
}
