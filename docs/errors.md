# Error responses

Every error keeps its HTTP status and the historical `detail` field, and adds a structured `error` object:

```json
{
  "detail": "Token is not authorized for this agent inbox",
  "error": {
    "code": "forbidden_inbox",
    "message": "Token is not authorized for this agent inbox",
    "details": null,
    "fix": "Use the key issued for this inbox.",
    "docs": "docs/errors.md#forbidden_inbox"
  }
}
```

- `error.code` is stable and safe to branch on; `message` is for humans and may change.
- `error.fix` is a short next step and `error.docs` points to this page's entry for the code. Both are present for every code in the table below and omitted for codes that are not listed.
- Validation failures (`422`) set `code` to `validation_error` and `details` to a list of `{path, message, type}`; `detail` keeps FastAPI's original list.
- Rate limits (`429`) send a `Retry-After` header and `error.details.retry_after_seconds`.
- An unrecognized code should be handled by its HTTP status.
- The TypeScript and Python SDKs expose `code`, `details` and `retryAfterSeconds` / `retry_after_seconds` on their error type, with `RateLimitError` and `IdempotencyKeyReuseError` subclasses; `oae` prints the code (`error: ... [rate_limited]: ...`).

| Code | HTTP | Meaning | Retry guidance |
|---|---|---|---|
| <a id="bad_request"></a>`bad_request` | 400 | The request is malformed. | Fix the request. |
| <a id="confirmation_required"></a>`confirmation_required` | 400 | A destructive action needs an explicit confirmation parameter. | Resend with the confirmation. |
| <a id="idempotency_key_invalid"></a>`idempotency_key_invalid` | 400 | The Idempotency-Key header is empty or longer than 128 characters. | Send a key of 1-128 characters. |
| <a id="invalid_action"></a>`invalid_action` | 400 | The requested action is not supported. | Use a documented action. |
| <a id="invalid_csv"></a>`invalid_csv` | 400 | A CSV upload is malformed, has bad headers, or has no usable rows. | Fix the file. |
| <a id="invalid_cursor"></a>`invalid_cursor` | 400 | The pagination cursor is malformed or was issued for different filters. | Restart from the first page. |
| <a id="invalid_domain"></a>`invalid_domain` | 400 | The domain name is not valid. | Fix the domain. |
| <a id="invalid_filter"></a>`invalid_filter` | 400 | A filter value (for example status) is not recognized. | Use a documented value. |
| <a id="invalid_provider_payload"></a>`invalid_provider_payload` | 400 | A provider notification payload is malformed. | Do not retry. |
| <a id="smtp_not_configured"></a>`smtp_not_configured` | 400 | The SMTP relay is not configured. | Configure the relay. |
| <a id="unauthorized"></a>`unauthorized` | 401 | Missing, malformed, expired or revoked credential. | Do not retry; supply a valid key. |
| <a id="admin_required"></a>`admin_required` | 403 | Only the administrator credential may do this. | Use the administrator key. |
| <a id="forbidden"></a>`forbidden` | 403 | The credential is valid but the action is not allowed. | Do not retry. |
| <a id="forbidden_inbox"></a>`forbidden_inbox` | 403 | The key belongs to a different inbox. | Use the key issued for this inbox. |
| <a id="forbidden_scope"></a>`forbidden_scope` | 403 | The key's scope does not permit this operation (for example a read-only key sending mail). | Use a key with the needed scope. |
| <a id="invalid_provider_signature"></a>`invalid_provider_signature` | 403 | A provider (SNS) notification failed signature, topic or certificate checks. | Do not retry; fix provider configuration. |
| <a id="reviewer_required"></a>`reviewer_required` | 403 | A reviewer or administrator credential is required. | Use a reviewer key. |
| <a id="feature_not_configured"></a>`feature_not_configured` | 404 | The optional feature is not configured on this server. | Configure the feature. |
| <a id="not_found"></a>`not_found` | 404 | The resource does not exist or is not visible to this credential. | Do not retry unchanged. |
| <a id="already_exists"></a>`already_exists` | 409 | A resource with the same unique value already exists. | Use the existing resource or a different value. |
| <a id="ambiguous_target"></a>`ambiguous_target` | 409 | The address exists in several inboxes; name the inbox. | Pass agent_email. |
| <a id="concurrent_modification"></a>`concurrent_modification` | 409 | The resource changed while handling the request. | Retry once. |
| <a id="conflict"></a>`conflict` | 409 | The request conflicts with the current state. | Re-read the resource, then retry if still valid. |
| <a id="delivery_state_uncertain"></a>`delivery_state_uncertain` | 409 | Delivery of the message is unknown; retrying may send a duplicate. | Retry only with confirm_duplicate_risk=true. |
| <a id="invalid_state"></a>`invalid_state` | 409 | The resource is not in a state that allows this action. | Check its status first. |
| <a id="limit_exceeded"></a>`limit_exceeded` | 409 | A per-inbox limit was reached. | Remove an item or ask for a higher limit. |
| <a id="review_already_resolved"></a>`review_already_resolved` | 409 | The review was already approved or rejected. | Do not retry. |
| <a id="review_pending"></a>`review_pending` | 409 | The message is waiting for review and cannot be changed that way. | Resolve the review first. |
| <a id="send_in_progress"></a>`send_in_progress` | 409 | The scheduled message is already being submitted. | Do not retry; check message status. |
| <a id="webhook_disabled"></a>`webhook_disabled` | 409 | The webhook endpoint is disabled. | Enable it, then retry. |
| <a id="attachment_limit_exceeded"></a>`attachment_limit_exceeded` | 413 | Too many or too large attachments. | Reduce attachments. |
| <a id="attachment_too_large"></a>`attachment_too_large` | 413 | Inline attachment exceeds the size limit. | Use the signed download URL instead. |
| <a id="payload_too_large"></a>`payload_too_large` | 413 | The request body or upload exceeds the size limit. | Send less data. |
| <a id="idempotency_key_reuse"></a>`idempotency_key_reuse` | 422 | The Idempotency-Key was already used with a different request. | Use a fresh key for a different request. |
| <a id="invalid_attachment"></a>`invalid_attachment` | 422 | An attachment is missing fields, has a bad type, or is not valid base64. | Fix the attachment. |
| <a id="invalid_request"></a>`invalid_request` | 422 | The request is understood but semantically invalid. | Fix the request. |
| <a id="invalid_template"></a>`invalid_template` | 422 | A template has invalid placeholder syntax, data, or rendered size. | Fix the template or data. |
| <a id="suppressed_recipient"></a>`suppressed_recipient` | 422 | The inbox has suppressed email to this recipient. | Do not retry; remove the suppression first if appropriate. |
| <a id="validation_error"></a>`validation_error` | 422 | The request body, query or path failed validation; `error.details` lists each field. | Fix the request. |
| <a id="rate_limited"></a>`rate_limited` | 429 | Too many requests from this client. | Wait `Retry-After` seconds, then retry. |
| <a id="internal_error"></a>`internal_error` | 500 | Unexpected server error. | Retry with backoff; report if it persists. |
| <a id="not_implemented"></a>`not_implemented` | 501 | The operation is not implemented. | Do not retry. |
| <a id="upstream_error"></a>`upstream_error` | 502 | An upstream service failed. | Retry with backoff. |
| <a id="encryption_key_missing"></a>`encryption_key_missing` | 503 | A required encryption key is not configured or unusable. | Operator must set SMTP_CONFIG_ENCRYPTION_KEY. |
| <a id="service_unavailable"></a>`service_unavailable` | 503 | The service or a dependency is temporarily unavailable. | Retry with backoff. |
