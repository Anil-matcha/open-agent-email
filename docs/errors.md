# Error responses

Every error keeps its HTTP status and the historical `detail` field, and adds a structured `error` object:

```json
{
  "detail": "Token is not authorized for this agent inbox",
  "error": {"code": "forbidden_inbox", "message": "Token is not authorized for this agent inbox", "details": null}
}
```

- `error.code` is stable and safe to branch on; `message` is for humans and may change.
- Validation failures (`422`) set `code` to `validation_error` and `details` to a list of `{path, message, type}`; `detail` keeps FastAPI's original list.
- Rate limits (`429`) send a `Retry-After` header and `error.details.retry_after_seconds`.
- An unrecognized code should be handled by its HTTP status.
- The TypeScript and Python SDKs expose `code`, `details` and `retryAfterSeconds` / `retry_after_seconds` on their error type, with `RateLimitError` and `IdempotencyKeyReuseError` subclasses; `oae` prints the code (`error: ... [rate_limited]: ...`).

| Code | HTTP | Meaning | Retry guidance |
|---|---|---|---|
| `bad_request` | 400 | The request is malformed. | Fix the request. |
| `confirmation_required` | 400 | A destructive action needs an explicit confirmation parameter. | Resend with the confirmation. |
| `idempotency_key_invalid` | 400 | The Idempotency-Key header is empty or longer than 128 characters. | Send a key of 1-128 characters. |
| `invalid_action` | 400 | The requested action is not supported. | Use a documented action. |
| `invalid_csv` | 400 | A CSV upload is malformed, has bad headers, or has no usable rows. | Fix the file. |
| `invalid_cursor` | 400 | The pagination cursor is malformed or was issued for different filters. | Restart from the first page. |
| `invalid_domain` | 400 | The domain name is not valid. | Fix the domain. |
| `invalid_filter` | 400 | A filter value (for example status) is not recognized. | Use a documented value. |
| `invalid_provider_payload` | 400 | A provider notification payload is malformed. | Do not retry. |
| `smtp_not_configured` | 400 | The SMTP relay is not configured. | Configure the relay. |
| `unauthorized` | 401 | Missing, malformed, expired or revoked credential. | Do not retry; supply a valid key. |
| `admin_required` | 403 | Only the administrator credential may do this. | Use the administrator key. |
| `forbidden` | 403 | The credential is valid but the action is not allowed. | Do not retry. |
| `forbidden_inbox` | 403 | The key belongs to a different inbox. | Use the key issued for this inbox. |
| `forbidden_scope` | 403 | The key's scope does not permit this operation (for example a read-only key sending mail). | Use a key with the needed scope. |
| `invalid_provider_signature` | 403 | A provider (SNS) notification failed signature, topic or certificate checks. | Do not retry; fix provider configuration. |
| `reviewer_required` | 403 | A reviewer or administrator credential is required. | Use a reviewer key. |
| `feature_not_configured` | 404 | The optional feature is not configured on this server. | Configure the feature. |
| `not_found` | 404 | The resource does not exist or is not visible to this credential. | Do not retry unchanged. |
| `already_exists` | 409 | A resource with the same unique value already exists. | Use the existing resource or a different value. |
| `ambiguous_target` | 409 | The address exists in several inboxes; name the inbox. | Pass agent_email. |
| `concurrent_modification` | 409 | The resource changed while handling the request. | Retry once. |
| `conflict` | 409 | The request conflicts with the current state. | Re-read the resource, then retry if still valid. |
| `delivery_state_uncertain` | 409 | Delivery of the message is unknown; retrying may send a duplicate. | Retry only with confirm_duplicate_risk=true. |
| `invalid_state` | 409 | The resource is not in a state that allows this action. | Check its status first. |
| `limit_exceeded` | 409 | A per-inbox limit was reached. | Remove an item or ask for a higher limit. |
| `review_already_resolved` | 409 | The review was already approved or rejected. | Do not retry. |
| `review_pending` | 409 | The message is waiting for review and cannot be changed that way. | Resolve the review first. |
| `send_in_progress` | 409 | The scheduled message is already being submitted. | Do not retry; check message status. |
| `webhook_disabled` | 409 | The webhook endpoint is disabled. | Enable it, then retry. |
| `attachment_limit_exceeded` | 413 | Too many or too large attachments. | Reduce attachments. |
| `attachment_too_large` | 413 | Inline attachment exceeds the size limit. | Use the signed download URL instead. |
| `payload_too_large` | 413 | The request body or upload exceeds the size limit. | Send less data. |
| `idempotency_key_reuse` | 422 | The Idempotency-Key was already used with a different request. | Use a fresh key for a different request. |
| `invalid_attachment` | 422 | An attachment is missing fields, has a bad type, or is not valid base64. | Fix the attachment. |
| `invalid_request` | 422 | The request is understood but semantically invalid. | Fix the request. |
| `invalid_template` | 422 | A template has invalid placeholder syntax, data, or rendered size. | Fix the template or data. |
| `suppressed_recipient` | 422 | The inbox has suppressed email to this recipient. | Do not retry; remove the suppression first if appropriate. |
| `validation_error` | 422 | The request body, query or path failed validation; `error.details` lists each field. | Fix the request. |
| `rate_limited` | 429 | Too many requests from this client. | Wait `Retry-After` seconds, then retry. |
| `internal_error` | 500 | Unexpected server error. | Retry with backoff; report if it persists. |
| `not_implemented` | 501 | The operation is not implemented. | Do not retry. |
| `upstream_error` | 502 | An upstream service failed. | Retry with backoff. |
| `encryption_key_missing` | 503 | A required encryption key is not configured or unusable. | Operator must set SMTP_CONFIG_ENCRYPTION_KEY. |
| `service_unavailable` | 503 | The service or a dependency is temporarily unavailable. | Retry with backoff. |
