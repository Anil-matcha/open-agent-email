# Monitoring

`GET /metrics` returns Prometheus text format. It needs a bearer token: the admin key, or a separate `METRICS_TOKEN` that can read nothing else (set it in the environment so a scraper never holds the admin key).

```yaml
scrape_configs:
  - job_name: open-agent-email
    metrics_path: /metrics
    authorization: {type: Bearer, credentials_file: /etc/prometheus/oae-metrics-token}
    static_configs: [{targets: ["mail.example.com:8000"]}]
```

Example alert rules are in [monitoring/alerts.yml](monitoring/alerts.yml). They are a starting point, not tested against your traffic; load them, then fire each one on purpose in staging (stop the worker, point the relay at a closed port) and confirm the alert reaches a human.

## Metrics

| Metric | Type | Meaning |
|---|---|---|
| `oae_outbox_jobs{status}` | gauge | Outbound jobs by status: `pending`, `processing`, `complete`, `failed`, `uncertain`, `blocked`. Always present, zero-filled. |
| `oae_outbox_oldest_pending_age_seconds` | gauge | Age of the oldest job that is due but not yet claimed; 0 when none. |
| `oae_outbox_oldest_processing_age_seconds` | gauge | Age of the longest-held claim; a claim older than five minutes becomes `uncertain`. |
| `oae_webhook_deliveries{status}` | gauge | Webhook deliveries by status: `pending`, `processing`, `delivered`, `failed`. |
| `oae_outbound_worker_up`, `oae_webhook_worker_up`, `oae_smtp_listener_up` | gauge | 1 when that component is running in this process. |
| `oae_outbound_worker_heartbeat_age_seconds` | gauge | Seconds since this process's outbound worker last polled. Absent until the worker has run once. |
| `oae_outbound_worker_iteration_errors_total` | counter | Worker loop iterations that raised. |
| `oae_smtp_active_sessions` | gauge | SMTP sessions open now. |
| `oae_smtp_sessions_total`, `oae_smtp_sessions_refused_total` | counter | Sessions admitted, and refused at the connection cap. |
| `oae_smtp_messages_total{result}` | counter | Inbound messages by outcome: `accepted`, `rejected` (permanent), `deferred` (temporary), `scanner_unavailable` (deferred because the attachment scanner was down). |

Database-derived gauges (`oae_outbox_*`, `oae_webhook_deliveries`) read the same data from any process. Counters and the heartbeat live in memory per process and reset on restart; scrape every process and sum counters. The REST send path's scanner failures are not counted here; they appear as HTTP errors.

## What the alerts cover

Readiness (`up`, worker, listener), a stalled or erroring worker, aging and growing outbox and webhook backlogs, uncertain SMTP outcomes, permanent failures, a saturated SMTP listener, and the attachment scanner. `/ready` remains the point-in-time check for load balancers; `/metrics` is for history and paging. Delivery of alerts (pager, chat) is deployment-specific and must be verified by you.
