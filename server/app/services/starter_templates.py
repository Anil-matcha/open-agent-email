"""Static catalog of starter email templates.

Every entry uses the same `{{variable}}` placeholder syntax as saved templates, so it is
validated and rendered by the existing safe engine (HTML values are escaped on render).
`sample_data` is used for previews only.
"""

from typing import Any

STARTER_TEMPLATES: list[dict[str, Any]] = [
    {
        "alias": "verification-code",
        "name": "Verification code",
        "description": "One-time code for signing in or confirming an address.",
        "subject": "Your {{product}} verification code: {{code}}",
        "body_text": "Your {{product}} verification code is {{code}}.\n\nIt expires in {{expires_minutes}} minutes. If you did not request it, you can ignore this email.",
        "body_html": "<p>Your {{product}} verification code is <strong>{{code}}</strong>.</p><p>It expires in {{expires_minutes}} minutes. If you did not request it, you can ignore this email.</p>",
        "sample_data": {"product": "Acme", "code": "482913", "expires_minutes": 10},
    },
    {
        "alias": "password-reset",
        "name": "Password reset",
        "description": "Link to choose a new password.",
        "subject": "Reset your {{product}} password",
        "body_text": "Hi {{name}},\n\nUse this link to choose a new password: {{reset_url}}\n\nThe link expires in {{expires_minutes}} minutes. If you did not ask for this, no action is needed.",
        "body_html": "<p>Hi {{name}},</p><p><a href=\"{{reset_url}}\">Choose a new password</a>. The link expires in {{expires_minutes}} minutes.</p><p>If you did not ask for this, no action is needed.</p>",
        "sample_data": {"product": "Acme", "name": "Sam", "reset_url": "https://example.com/reset/abc", "expires_minutes": 30},
    },
    {
        "alias": "welcome",
        "name": "Welcome",
        "description": "Greeting for a new user or customer.",
        "subject": "Welcome to {{product}}, {{name}}",
        "body_text": "Hi {{name}},\n\nWelcome to {{product}}. To get started: {{getting_started_url}}\n\nReply to this email if you need a hand.",
        "body_html": "<p>Hi {{name}},</p><p>Welcome to {{product}}. <a href=\"{{getting_started_url}}\">Get started here</a>.</p><p>Reply to this email if you need a hand.</p>",
        "sample_data": {"product": "Acme", "name": "Sam", "getting_started_url": "https://example.com/start"},
    },
    {
        "alias": "receipt",
        "name": "Order receipt",
        "description": "Confirmation of a completed purchase.",
        "subject": "Receipt {{order.number}} from {{product}}",
        "body_text": "Hi {{name}},\n\nThanks for your order {{order.number}}.\nTotal: {{order.total}} {{order.currency}}\nView it: {{order.url}}",
        "body_html": "<p>Hi {{name}},</p><p>Thanks for your order <strong>{{order.number}}</strong>.</p><p>Total: {{order.total}} {{order.currency}}</p><p><a href=\"{{order.url}}\">View order</a></p>",
        "sample_data": {"product": "Acme", "name": "Sam",
                        "order": {"number": "A-1042", "total": "49.00", "currency": "USD", "url": "https://example.com/orders/A-1042"}},
    },
    {
        "alias": "support-auto-ack",
        "name": "Support acknowledgement",
        "description": "Automatic reply confirming a support request was received.",
        "subject": "We received your request ({{ticket_id}})",
        "body_text": "Hi {{name}},\n\nThanks for contacting {{product}} support. Your request is {{ticket_id}} and we will reply within {{response_hours}} hours.\n\nA person may review this reply before it is sent.",
        "body_html": "<p>Hi {{name}},</p><p>Thanks for contacting {{product}} support. Your request is <strong>{{ticket_id}}</strong> and we will reply within {{response_hours}} hours.</p>",
        "sample_data": {"product": "Acme", "name": "Sam", "ticket_id": "T-2210", "response_hours": 24},
    },
    {
        "alias": "meeting-follow-up",
        "name": "Meeting follow-up",
        "description": "Summary and next steps after a meeting.",
        "subject": "Follow-up: {{meeting_title}}",
        "body_text": "Hi {{name}},\n\nThanks for your time today. Summary: {{summary}}\n\nNext step: {{next_step}} (by {{due_date}}).",
        "body_html": "<p>Hi {{name}},</p><p>Thanks for your time today.</p><p><strong>Summary:</strong> {{summary}}</p><p><strong>Next step:</strong> {{next_step}} (by {{due_date}}).</p>",
        "sample_data": {"name": "Sam", "meeting_title": "Q4 planning", "summary": "Agreed scope and timeline.",
                        "next_step": "Send draft proposal", "due_date": "Friday"},
    },
    {
        "alias": "outreach-intro",
        "name": "Outreach introduction",
        "description": "Short first-touch message. Send only to recipients who have agreed to hear from you.",
        "subject": "{{name}}, a quick idea for {{company}}",
        "body_text": "Hi {{name}},\n\nI work on {{sender_product}} and thought it could help {{company}} with {{pain_point}}.\n\nWould a short call next week be useful? If not, tell me and I will not follow up.",
        "body_html": "<p>Hi {{name}},</p><p>I work on {{sender_product}} and thought it could help {{company}} with {{pain_point}}.</p><p>Would a short call next week be useful? If not, tell me and I will not follow up.</p>",
        "sample_data": {"name": "Sam", "company": "Globex", "sender_product": "Acme", "pain_point": "slow onboarding"},
    },
    {
        "alias": "weekly-digest",
        "name": "Weekly digest",
        "description": "Periodic summary of activity.",
        "subject": "Your {{product}} week: {{stats.new_items}} new",
        "body_text": "Hi {{name}},\n\nThis week: {{stats.new_items}} new items, {{stats.completed}} completed.\nSee everything: {{dashboard_url}}",
        "body_html": "<p>Hi {{name}},</p><p>This week: <strong>{{stats.new_items}}</strong> new items, <strong>{{stats.completed}}</strong> completed.</p><p><a href=\"{{dashboard_url}}\">See everything</a></p>",
        "sample_data": {"product": "Acme", "name": "Sam", "stats": {"new_items": 12, "completed": 9},
                        "dashboard_url": "https://example.com/dashboard"},
    },
]

_BY_ALIAS = {item["alias"]: item for item in STARTER_TEMPLATES}


def get_starter(alias: str) -> dict[str, Any] | None:
    return _BY_ALIAS.get(alias)
