import { createHmac, timingSafeEqual } from "node:crypto";
import { WebhookSignatureError } from "./errors.js";

export const DEFAULT_TOLERANCE_SECONDS = 300;

export type HeaderSource = Headers | Record<string, string | string[] | undefined>;

export interface WebhookEvent<T = Record<string, unknown>> {
  id: string;
  type: string;
  timestamp: number;
  payload: T;
}

function header(headers: HeaderSource, name: string): string | undefined {
  if (typeof (headers as Headers).get === "function") return (headers as Headers).get(name) ?? undefined;
  const wanted = name.toLowerCase();
  for (const [key, value] of Object.entries(headers as Record<string, string | string[] | undefined>)) {
    if (key.toLowerCase() === wanted) return Array.isArray(value) ? value[0] : value;
  }
  return undefined;
}

/** Legacy signature for one secret: HMAC-SHA256 over `<timestamp>.<event id>.<raw body>`, hex, prefixed `sha256=`. */
export function signWebhook(secret: string, timestamp: string | number, eventId: string, body: string | Uint8Array): string {
  const mac = createHmac("sha256", secret);
  mac.update(`${timestamp}.${eventId}.`);
  mac.update(body);
  return `sha256=${mac.digest("hex")}`;
}

/** Current signature for one secret: HMAC-SHA256 over `<timestamp>.<raw body>`, lowercase hex (the `v1=` value). */
export function signWebhookV1(secret: string, timestamp: string | number, body: string | Uint8Array): string {
  const mac = createHmac("sha256", secret);
  mac.update(`${timestamp}.`);
  mac.update(body);
  return mac.digest("hex");
}

/** Parse `t=<unix>,v1=<hex>[,v1=<hex>]`. Unknown keys are ignored so the format can grow. */
function parseSignatureHeader(value: string): { timestamp?: string; v1: string[] } {
  const parsed: { timestamp?: string; v1: string[] } = { v1: [] };
  for (const part of value.split(",")) {
    const eq = part.indexOf("=");
    if (eq < 0) continue;
    const key = part.slice(0, eq).trim();
    const val = part.slice(eq + 1).trim();
    if (key === "t") parsed.timestamp = val;
    else if (key === "v1") parsed.v1.push(val);
  }
  return parsed;
}

/** Compare against every candidate so timing does not reveal which one matched. */
function anyMatch(expected: string, candidates: string[]): boolean {
  const want = Buffer.from(expected);
  return candidates.reduce((hit, candidate) => {
    const given = Buffer.from(candidate);
    return (given.length === want.length && timingSafeEqual(given, want)) || hit;
  }, false);
}

/**
 * Verify a delivery and return the parsed event. Pass the RAW request body and the request headers.
 *
 * Two schemes are accepted. When `Webhook-Signature` (`t=<unix>,v1=<hex>`) is present it is used and
 * must verify; otherwise the legacy `X-Webhook-Event-Id`, `X-Webhook-Timestamp` and `X-Webhook-Signature`
 * headers are used. Pass several secrets while a rotation is in flight; the server signs with both for
 * 24 hours and any match is accepted.
 */
export function constructEvent<T = Record<string, unknown>>(
  body: string | Uint8Array,
  headers: HeaderSource,
  secrets: string | string[],
  opts: { toleranceSeconds?: number; now?: number } = {},
): WebhookEvent<T> {
  const secretList = (Array.isArray(secrets) ? secrets : [secrets]).filter(Boolean);
  if (secretList.length === 0) throw new WebhookSignatureError("No signing secret provided");
  const tolerance = opts.toleranceSeconds ?? DEFAULT_TOLERANCE_SECONDS;
  const now = opts.now ?? Math.floor(Date.now() / 1000);

  const currentHeader = header(headers, "webhook-signature");
  let timestamp: string | undefined;
  let eventId = header(headers, "x-webhook-event-id");
  let matched: boolean;

  if (currentHeader) {
    const { timestamp: t, v1 } = parseSignatureHeader(currentHeader);
    timestamp = t;
    if (!timestamp || v1.length === 0) throw new WebhookSignatureError("Malformed Webhook-Signature header");
    if (!/^\d{1,12}$/.test(timestamp)) throw new WebhookSignatureError("Invalid webhook timestamp");
    if (Math.abs(now - Number(timestamp)) > tolerance) throw new WebhookSignatureError("Webhook timestamp is outside the allowed window");
    matched = secretList.some((secret) => anyMatch(signWebhookV1(secret, timestamp as string, body), v1));
  } else {
    timestamp = header(headers, "x-webhook-timestamp");
    const signatureHeader = header(headers, "x-webhook-signature");
    if (!eventId || !timestamp || !signatureHeader) throw new WebhookSignatureError("Missing webhook signature headers");
    if (!/^\d{1,12}$/.test(timestamp)) throw new WebhookSignatureError("Invalid webhook timestamp");
    if (Math.abs(now - Number(timestamp)) > tolerance) throw new WebhookSignatureError("Webhook timestamp is outside the allowed window");
    const provided = signatureHeader.split(",").map((s) => s.trim()).filter(Boolean);
    matched = secretList.some((secret) => anyMatch(signWebhook(secret, timestamp as string, eventId as string, body), provided));
  }
  if (!matched) throw new WebhookSignatureError("Webhook signature does not match");

  let payload: T;
  try {
    payload = JSON.parse(typeof body === "string" ? body : Buffer.from(body).toString("utf8")) as T;
  } catch {
    throw new WebhookSignatureError("Webhook body is not valid JSON");
  }
  const fields = payload as unknown as { id?: unknown; type?: unknown };
  eventId = eventId ?? (typeof fields.id === "string" ? fields.id : "");
  const type = header(headers, "webhook-event-type") ?? header(headers, "x-webhook-event")
    ?? (typeof fields.type === "string" ? fields.type : "");
  return { id: eventId, type, timestamp: Number(timestamp), payload };
}
