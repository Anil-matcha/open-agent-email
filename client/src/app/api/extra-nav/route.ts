// Navigation additions for deployments that extend the dashboard. A hosted layer replaces this file to add
// links under "More" and to hide entries the signed-in user may not use. The open core adds nothing.
export const dynamic = "force-dynamic";

export async function GET() {
  return Response.json({ items: [] as { name: string; href: string }[], hide: [] as string[] });
}
