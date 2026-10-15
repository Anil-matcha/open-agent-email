/** Error raised for any failed API call. */
export class AgentEmailError extends Error {
  readonly status: number | undefined;
  /** Legacy `detail` field of the response body (string, list, or the whole body). */
  readonly detail: unknown;
  /** Stable machine code from the server's error catalog (for example `rate_limited`). */
  readonly code: string | undefined;
  /** Structured `error.details` (validation errors are a list of `{path, message, type}`). */
  readonly details: unknown;
  /** Seconds to wait before retrying; set on rate limits from `Retry-After` / `error.details`. */
  readonly retryAfterSeconds: number | undefined;
  /** Suggested next step, when the server provides one. */
  readonly fix: string | undefined;
  /** Link to the error's documentation, when the server provides one. */
  readonly docs: string | undefined;
  constructor(
    message: string,
    opts: { status?: number; detail?: unknown; code?: string; details?: unknown; retryAfterSeconds?: number; fix?: string; docs?: string } = {},
  ) {
    super(message);
    this.name = "AgentEmailError";
    this.status = opts.status;
    this.detail = opts.detail;
    this.code = opts.code;
    this.details = opts.details;
    this.retryAfterSeconds = opts.retryAfterSeconds;
    this.fix = opts.fix;
    this.docs = opts.docs;
  }
}

/** HTTP 429 / code `rate_limited`. Wait `retryAfterSeconds` before retrying. */
export class RateLimitError extends AgentEmailError {
  constructor(message: string, opts: ConstructorParameters<typeof AgentEmailError>[1] = {}) {
    super(message, opts);
    this.name = "RateLimitError";
  }
}

/** Code `idempotency_key_reuse`: the key was already used with a different request. */
export class IdempotencyKeyReuseError extends AgentEmailError {
  constructor(message: string, opts: ConstructorParameters<typeof AgentEmailError>[1] = {}) {
    super(message, opts);
    this.name = "IdempotencyKeyReuseError";
  }
}

/** Build the right error subclass from a failed response's parsed body and `Retry-After` header. */
export function errorFromResponse(
  prefix: string,
  status: number,
  data: unknown,
  retryAfterHeader?: string | null,
): AgentEmailError {
  const body = data && typeof data === "object" ? (data as Record<string, unknown>) : undefined;
  const structured =
    body && body.error && typeof body.error === "object" ? (body.error as Record<string, unknown>) : undefined;
  const detail = body && "detail" in body ? body.detail : data;
  const code = typeof structured?.code === "string" ? structured.code : undefined;
  const details = structured?.details ?? undefined;
  const fix = typeof structured?.fix === "string" ? structured.fix : undefined;
  const docs = typeof structured?.docs === "string" ? structured.docs : undefined;

  let retryAfterSeconds: number | undefined;
  const fromDetails =
    details && typeof details === "object" ? (details as Record<string, unknown>).retry_after_seconds : undefined;
  const header = retryAfterHeader ? Number(retryAfterHeader) : NaN;
  if (typeof fromDetails === "number" && Number.isFinite(fromDetails)) retryAfterSeconds = fromDetails;
  else if (Number.isFinite(header) && header >= 0) retryAfterSeconds = header;

  const text =
    typeof detail === "string" ? detail : typeof structured?.message === "string" ? structured.message : JSON.stringify(detail);
  const message = `${prefix} failed with ${status}${code ? ` [${code}]` : ""}: ${text ?? "no detail"}`;
  const opts = { status, detail, code, details, retryAfterSeconds, fix, docs };
  if (code === "rate_limited" || status === 429) return new RateLimitError(message, opts);
  if (code === "idempotency_key_reuse") return new IdempotencyKeyReuseError(message, opts);
  return new AgentEmailError(message, opts);
}

/** Raised when a webhook delivery fails signature or timestamp verification. */
export class WebhookSignatureError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WebhookSignatureError";
  }
}
