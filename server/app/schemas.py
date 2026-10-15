from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, timezone

MAX_SEND_AT_DAYS = 90


def normalize_send_at(value):
    """Normalize an offset-qualified scheduling instant to naive UTC for storage."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scheduled_at must include a timezone offset")
    value = value.astimezone(timezone.utc).replace(tzinfo=None)
    now = datetime.utcnow()
    if value <= now:
        raise ValueError("send_at must be in the future")
    if value > now + timedelta(days=MAX_SEND_AT_DAYS):
        raise ValueError(f"send_at must be within {MAX_SEND_AT_DAYS} days")
    return value

# --- Agent Schemas ---
class AgentCreate(BaseModel):
    email: str = Field(..., example="support@agents.local")
    display_name: Optional[str] = Field(None, example="Support AI Agent")  # defaults to the address local part
    description: Optional[str] = "Tier-1 Autonomous Email Support"
    threat_sensitivity: str = Field("medium", example="medium")  # off, low, medium, high
    hitl_enabled: bool = True
    auto_reply_enabled: bool = False
    auto_reply_prompt: Optional[str] = "You are an autonomous AI assistant replying concisely via email."

class AgentUpdate(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    threat_sensitivity: Optional[str] = None
    hitl_enabled: Optional[bool] = None
    auto_reply_enabled: Optional[bool] = None
    auto_reply_prompt: Optional[str] = None

class AgentResponse(BaseModel):
    id: str
    email: str
    display_name: str
    description: str
    status: str
    threat_sensitivity: str
    hitl_enabled: bool
    auto_reply_enabled: bool
    auto_reply_prompt: str
    deleted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# --- Contact Schemas ---
class ContactCreate(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=1, max_length=255)
    company: str = Field("", max_length=255)
    notes: str = Field("", max_length=4000)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_contact_metadata(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        import json
        if not isinstance(value, dict) or len(value) > 50:
            raise ValueError("Metadata must be a flat object with at most 50 keys")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 64:
                raise ValueError("Metadata keys must be nonempty strings of at most 64 characters")
            if item is not None and not isinstance(item, (str, int, float, bool)):
                raise ValueError("Metadata values must be flat scalar values")
            if isinstance(item, str) and len(item) > 1000:
                raise ValueError("Metadata string values must be at most 1,000 characters")
        try:
            serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("Metadata values must be valid JSON scalars") from exc
        if len(serialized.encode("utf-8")) > 8192:
            raise ValueError("Metadata must be at most 8 KB")
        return value

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        if len(normalized) > 255:
            raise ValueError("Email address must be at most 255 characters")
        return normalized

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Name cannot be empty")
        return trimmed

    @field_validator("company", "notes")
    @classmethod
    def trim_optional_text(cls, value: str) -> str:
        return value.strip()


class ContactUpdate(BaseModel):
    email: Optional[EmailStr] = None
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    company: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = Field(None, max_length=4000)
    metadata_json: Optional[Dict[str, Any]] = Field(None, validation_alias="metadata", serialization_alias="metadata")

    @field_validator("metadata_json")
    @classmethod
    def validate_contact_metadata(cls, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if value is None:
            return None
        return ContactCreate(email="metadata@example.com", name="metadata", metadata=value).metadata

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        if len(normalized) > 255:
            raise ValueError("Email address must be at most 255 characters")
        return normalized

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Name cannot be empty")
        return trimmed

    @field_validator("company", "notes")
    @classmethod
    def trim_optional_text(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if value is not None else None


class ContactResponse(BaseModel):
    id: str
    agent_email: str
    email: EmailStr
    name: str
    company: str
    notes: str
    metadata: Dict[str, Any] = Field(default_factory=dict, validation_alias="metadata_json")
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ContactConsentWrite(BaseModel):
    action: str = Field(..., pattern="^(granted|revoked)$")
    source: str = Field(..., min_length=1, max_length=255)
    evidence: str = Field("", max_length=1000)
    note: str = Field("", max_length=1000)

    @field_validator("source")
    @classmethod
    def trim_source(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Consent source cannot be empty")
        return trimmed

    @field_validator("evidence", "note")
    @classmethod
    def trim_optional_consent_text(cls, value: str) -> str:
        return value.strip()


class ContactConsentEventResponse(BaseModel):
    id: int
    agent_email: str
    contact_id: str
    action: str
    source: str
    evidence: str
    note: str
    created_at: datetime

    class Config:
        from_attributes = True


class ContactConsentHistoryResponse(BaseModel):
    current: str
    events: List[ContactConsentEventResponse]


class ContactEngagementWrite(BaseModel):
    contact_id: str = Field(..., min_length=1, max_length=64)
    stage: str = Field("new", pattern="^(new|contacted|waiting|closed)$")
    next_due_at: Optional[datetime] = None
    notes: str = Field("", max_length=4000)

    @field_validator("notes")
    @classmethod
    def trim_engagement_notes(cls, value: str) -> str:
        return value.strip()

    @field_validator("next_due_at")
    @classmethod
    def normalize_due_time(cls, value: Optional[datetime]) -> Optional[datetime]:
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value and value.tzinfo else value


class ContactEngagementUpdate(BaseModel):
    stage: Optional[str] = Field(None, pattern="^(new|contacted|waiting|closed)$")
    next_due_at: Optional[datetime] = None
    notes: Optional[str] = Field(None, max_length=4000)

    @field_validator("notes")
    @classmethod
    def trim_engagement_notes(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if value is not None else None

    @field_validator("next_due_at")
    @classmethod
    def normalize_due_time(cls, value: Optional[datetime]) -> Optional[datetime]:
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value and value.tzinfo else value


class ContactEngagementResponse(BaseModel):
    id: str
    agent_email: str
    contact_id: str
    contact_email: str
    contact_name: str
    stage: str
    next_due_at: Optional[datetime]
    notes: str
    replied_at: Optional[datetime]
    replied: bool
    suppressed: bool
    created_at: datetime
    updated_at: datetime


class RecipientSuppressionCreate(BaseModel):
    email: EmailStr
    reason: Optional[str] = Field(None, max_length=500)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        if len(normalized) > 255:
            raise ValueError("Email address must be at most 255 characters")
        return normalized

    @field_validator("email")
    @classmethod
    def bound_normalized_email(cls, value: EmailStr) -> EmailStr:
        if len(str(value)) > 255:
            raise ValueError("Email address must be at most 255 characters")
        return value

    @field_validator("reason")
    @classmethod
    def trim_reason(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return value.strip() or None


class RecipientSuppressionResponse(BaseModel):
    id: str
    agent_email: str
    email: EmailStr
    reason: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# --- Reusable email template schemas ---
TEMPLATE_PART_MAX = 100_000


class EmailTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    alias: Optional[str] = Field(None, min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9._-]*$")
    subject: str = Field(..., min_length=1, max_length=512)
    body_text: str = Field("", max_length=TEMPLATE_PART_MAX)
    body_html: str = Field("", max_length=TEMPLATE_PART_MAX)

    @field_validator("name", "subject", "body_text", "body_html")
    @classmethod
    def trim_template_fields(cls, value: str) -> str:
        return value.strip()

    @field_validator("name")
    @classmethod
    def require_template_name(cls, value: str) -> str:
        if not value:
            raise ValueError("Name cannot be empty")
        return value

    @field_validator("subject")
    @classmethod
    def validate_template_subject(cls, value: str) -> str:
        if not value or "\r" in value or "\n" in value:
            raise ValueError("Subject must be non-empty and cannot contain line breaks")
        return value

    @field_validator("body_html")
    @classmethod
    def require_template_body(cls, value: str, info) -> str:
        text_body = info.data.get("body_text", "")
        if not value and not text_body:
            raise ValueError("At least one of body_text or body_html is required")
        return value


class EmailTemplateUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    alias: Optional[str] = Field(None, max_length=128, pattern=r"^$|^[A-Za-z][A-Za-z0-9._-]*$")
    subject: Optional[str] = Field(None, min_length=1, max_length=512)
    body_text: Optional[str] = Field(None, max_length=TEMPLATE_PART_MAX)
    body_html: Optional[str] = Field(None, max_length=TEMPLATE_PART_MAX)

    @field_validator("name", "subject", "body_text", "body_html")
    @classmethod
    def trim_template_patch(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if value is not None else None


class EmailTemplateResponse(BaseModel):
    id: str
    agent_email: str
    name: str
    alias: Optional[str] = None
    subject: str
    body_text: str
    body_html: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class EmailTemplatePreviewRequest(BaseModel):
    subject: str = Field(..., min_length=1, max_length=512)
    body_text: str = Field("", max_length=TEMPLATE_PART_MAX)
    body_html: str = Field("", max_length=TEMPLATE_PART_MAX)
    data: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("subject")
    @classmethod
    def validate_preview_subject(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("Subject cannot contain line breaks")
        return value


class EmailTemplateRenderData(BaseModel):
    data: Dict[str, Any] = Field(default_factory=dict)


# --- Threat Scan Schemas ---
class ThreatDetail(BaseModel):
    threat_type: str
    severity: str
    confidence: float
    description: str
    matched_pattern: Optional[str] = None

class ThreatScanResult(BaseModel):
    threat_score: float
    verdict: str  # allow, review, block
    detected_threats: List[ThreatDetail]
    analysis_summary: str


# --- Message Schemas ---

def _apply_input_aliases(data, aliases):
    """Copy alias keys onto their canonical names; reject a conflicting pair."""
    if not isinstance(data, dict):
        return data
    data = dict(data)
    for alias, canonical in aliases.items():
        if alias in data:
            value = data.pop(alias)
            if canonical in data and data[canonical] != value:
                raise ValueError(f"{alias} and {canonical} must match when both are provided")
            data.setdefault(canonical, value)
    return data


_SEND_ALIASES = {"to": "recipient", "text": "body_text", "html": "body_html"}
_REPLY_ALIASES = {"text": "body_text", "html": "body_html"}

class MessageAttachmentInput(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    content_type: str = Field("application/octet-stream", max_length=255)
    content_base64: str = Field(..., min_length=1)


class MessageSendRequest(BaseModel):
    recipient: str = Field(..., min_length=3, max_length=320, example="customer@example.com")
    subject: Optional[str] = Field(None, max_length=512, example="Update on your support ticket #492")
    body_text: Optional[str] = Field(None, max_length=100_000, example="Hello! We have reviewed your inquiry and resolved the issue.")
    body_html: Optional[str] = None
    template_id: Optional[str] = Field(None, min_length=1, max_length=64)
    template_alias: Optional[str] = Field(None, min_length=1, max_length=128)
    template_data: Dict[str, Any] = Field(default_factory=dict)
    thread_id: Optional[str] = None
    conversation_id: Optional[str] = Field(None, min_length=1, max_length=255)
    attachments: Optional[List[MessageAttachmentInput]] = None
    scheduled_at: Optional[datetime] = Field(None, description="Defer submission until this offset-qualified time (at most 90 days ahead)")
    send_at: Optional[datetime] = Field(None, description="Deprecated alias for scheduled_at")

    @model_validator(mode="before")
    @classmethod
    def _input_aliases(cls, data):
        """Also accepts `to`, `text` and `html` for `recipient`, `body_text` and `body_html`."""
        return _apply_input_aliases(data, _SEND_ALIASES)

    @field_validator("scheduled_at", "send_at")
    @classmethod
    def _validate_send_at(cls, value):
        return normalize_send_at(value)

    @model_validator(mode="after")
    def _scheduled_aliases(self):
        if self.scheduled_at is not None and self.send_at is not None and self.scheduled_at != self.send_at:
            raise ValueError("scheduled_at and send_at must match when both are provided")
        self.scheduled_at = self.scheduled_at or self.send_at
        return self

class MessageReplyRequest(BaseModel):
    body_text: Optional[str] = Field(None, max_length=100_000, example="Thank you for confirming. I am closing this ticket now.")
    body_html: Optional[str] = None
    template_id: Optional[str] = Field(None, min_length=1, max_length=64)
    template_alias: Optional[str] = Field(None, min_length=1, max_length=128)
    template_data: Dict[str, Any] = Field(default_factory=dict)
    conversation_id: Optional[str] = Field(None, min_length=1, max_length=255)
    attachments: Optional[List[MessageAttachmentInput]] = None
    scheduled_at: Optional[datetime] = Field(None, description="Defer submission until this offset-qualified time (at most 90 days ahead)")
    send_at: Optional[datetime] = Field(None, description="Deprecated alias for scheduled_at")
    reply_all: bool = Field(False, description="Accepted for client compatibility; messages have a single recipient, so true is rejected")

    @model_validator(mode="before")
    @classmethod
    def _input_aliases(cls, data):
        """Also accepts `text` and `html` for `body_text` and `body_html`."""
        return _apply_input_aliases(data, _REPLY_ALIASES)

    @field_validator("scheduled_at", "send_at")
    @classmethod
    def _validate_send_at(cls, value):
        return normalize_send_at(value)

    @model_validator(mode="after")
    def _scheduled_aliases(self):
        if self.scheduled_at is not None and self.send_at is not None and self.scheduled_at != self.send_at:
            raise ValueError("scheduled_at and send_at must match when both are provided")
        self.scheduled_at = self.scheduled_at or self.send_at
        return self

class MessageForwardRequest(BaseModel):
    recipient: str = Field(..., min_length=3, max_length=320, example="support-lead@example.com")
    note: Optional[str] = Field(None, max_length=5000, example="Please review the refund request before we reply.")
    body_text: Optional[str] = Field(None, max_length=100_000, description="Optional plain-text content for the forwarded email")
    body_html: Optional[str] = Field(None, description="Optional HTML content for the forwarded email")
    attachments: Optional[List[MessageAttachmentInput]] = None

    @model_validator(mode="before")
    @classmethod
    def _input_aliases(cls, data):
        """Also accepts `to`, `text` and `html` for `recipient`, `body_text` and `body_html`."""
        return _apply_input_aliases(data, _SEND_ALIASES)

class MessageResponse(BaseModel):
    id: str
    agent_email: str
    direction: str
    is_read: bool
    sender: str
    recipient: str
    subject: str
    body_text: str
    body_html: Optional[str]
    thread_id: str
    conversation_id: Optional[str] = None
    raw_message_id: Optional[str] = None
    in_reply_to: Optional[str]
    references: Optional[str]
    rfc_message_id: str
    spf_status: str
    dkim_status: str
    dmarc_status: str
    auth_evidence: Optional[Dict[str, Any]] = None
    authentication: Optional[Dict[str, Any]] = None
    verified_domain: Optional[str]
    delivery_error: Optional[str] = None
    provider_delivery_status: Optional[str] = None
    provider_delivery_error: Optional[str] = None
    status: str
    threat_score: float
    threat_details: str
    labels: List[str] = Field(default_factory=list)
    scheduled_for: Optional[datetime] = None
    scheduled_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class MessageLabelsUpdate(BaseModel):
    labels: Optional[List[str]] = Field(None, max_length=20, description="Replace the whole label set")
    add_labels: Optional[List[str]] = Field(None, max_length=20, description="Labels to add to the current set")
    remove_labels: Optional[List[str]] = Field(None, max_length=20, description="Labels to remove from the current set")

    @model_validator(mode="after")
    def _one_form(self):
        if self.labels is None and self.add_labels is None and self.remove_labels is None:
            raise ValueError("Provide labels, or add_labels and/or remove_labels")
        if self.labels is not None and (self.add_labels is not None or self.remove_labels is not None):
            raise ValueError("labels cannot be combined with add_labels or remove_labels")
        return self

    @field_validator("labels", "add_labels", "remove_labels")
    @classmethod
    def _clean_labels(cls, value):
        if value is None:
            return None
        cleaned = []
        for label in value:
            label = label.strip()
            if not label or len(label) > 64 or any(ord(c) < 32 for c in label):
                raise ValueError("Labels must be 1-64 printable characters")
            if label not in cleaned:
                cleaned.append(label)
        return cleaned


class MessagePageResponse(BaseModel):
    items: List[MessageResponse]
    next_cursor: Optional[str] = None


class RecentOtpResponse(BaseModel):
    found: bool
    code: Optional[str] = None
    message_id: Optional[str] = None
    received_at: Optional[datetime] = None
    sender: Optional[str] = None
    subject: Optional[str] = None


class MessageAttachmentResponse(BaseModel):
    id: str
    message_id: str
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime
    download_url: Optional[str] = None

    class Config:
        from_attributes = True


# --- Review / HITL Schemas ---
class ReviewDecisionRequest(BaseModel):
    action: str = Field(..., example="approve")  # approve, reject
    notes: Optional[str] = None

class ReviewItemResponse(BaseModel):
    id: str
    message_id: str
    agent_email: str
    reason: str
    threat_score: float
    status: str
    reviewer_notes: Optional[str]
    reviewed_by: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime]
    message: Optional[MessageResponse] = None

    class Config:
        from_attributes = True


class OutboundJobResponse(BaseModel):
    id: str
    message_id: str
    status: str
    attempts: int
    available_at: datetime
    locked_at: Optional[datetime]
    last_error: Optional[str]
    retried_by: Optional[str]
    retried_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class OutboundJobPageResponse(BaseModel):
    items: List[OutboundJobResponse]
    next_cursor: Optional[str] = None


class OutboundRetryEventResponse(BaseModel):
    id: str
    job_id: str
    actor: str
    requested_at: datetime
    previous_attempts: int
    previous_error: Optional[str]
    previous_status: Optional[str] = None

    class Config:
        from_attributes = True


# --- Domain Schemas ---
class DomainCreate(BaseModel):
    domain: str = Field(..., example="agents.mycompany.com")

class DomainResponse(BaseModel):
    id: str
    domain: str
    status: str
    mx_record: str
    mx_status: str
    spf_record: str
    spf_status: str
    dkim_selector: str
    dkim_record: str
    dkim_status: str
    dmarc_record: str
    dmarc_status: str
    last_checked_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True

# --- SMTP Config Schemas ---
class SmtpConfigUpdate(BaseModel):
    host: str = Field("", example="smtp.gmail.com")
    port: str = Field("587", example="587")
    username: str = Field("", example="user@gmail.com")
    password: Optional[str] = Field(None, example="app_password")
    use_tls: bool = True
    use_ssl: bool = False
    from_email: str = Field("", example="agent@mycompany.com")
    enabled: bool = False

class SmtpConfigResponse(BaseModel):
    id: str
    host: str
    port: str
    username: str
    password_configured: bool
    password_encryption_enabled: bool
    use_tls: bool
    use_ssl: bool
    from_email: str
    enabled: bool
    updated_at: Optional[datetime] = None

class SmtpTestRequest(BaseModel):
    test_recipient: str = Field(..., example="recipient@example.com")

class SmtpTestResponse(BaseModel):
    success: bool
    message: str

# --- API Key Schemas ---
class ApiKeyCreate(BaseModel):
    name: str = Field(..., example="Cursor AI Integration")
    agent_email: str
    scope: str = Field("full_access", example="full_access", pattern="^(read_only|full_access|reviewer)$")
    expires_in_days: Optional[int] = Field(90, ge=1, le=3650)

class ApiKeyResponse(BaseModel):
    id: str
    name: str
    prefix: str
    scope: str
    agent_email: Optional[str] = None
    created_at: datetime
    expires_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class ApiKeyPageResponse(BaseModel):
    items: List[ApiKeyResponse]
    next_cursor: Optional[str] = None

class ApiKeyDeleteResponse(BaseModel):
    deleted: bool
    id: str

class ApiKeyCreatedResponse(BaseModel):
    id: str
    name: str
    key: str
    prefix: str
    scope: str
    agent_email: str
    created_at: datetime
    expires_at: Optional[datetime] = None


class AccountApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    scope: str = Field(..., pattern="^(account|agent)$")
    agent_email: Optional[str] = None
    expires_in_days: Optional[int] = Field(90, ge=1, le=3650)

    @model_validator(mode="after")
    def validate_scope_owner(self):
        if self.scope == "account" and self.agent_email is not None:
            raise ValueError("account-scoped keys cannot include agent_email")
        if self.scope == "agent" and not self.agent_email:
            raise ValueError("agent-scoped keys require agent_email")
        return self


class AccountApiKeyResponse(BaseModel):
    id: str
    name: str
    key_prefix: str
    scope: str
    agent_email: Optional[str] = None
    created_at: datetime
    expires_at: Optional[datetime] = None

    @classmethod
    def from_key(cls, key):
        return cls(id=key.id, name=key.name, key_prefix=key.prefix, scope=key.scope,
                   agent_email=key.agent_email, created_at=key.created_at, expires_at=key.expires_at)


class AccountApiKeyPageResponse(BaseModel):
    items: List[AccountApiKeyResponse]
    next_cursor: Optional[str] = None


class AccountApiKeyCreatedResponse(BaseModel):
    id: str
    name: str
    key: str
    key_prefix: str
    scope: str
    agent_email: Optional[str] = None
    created_at: datetime
    expires_at: Optional[datetime] = None

# --- Stats Schema ---
class StatsResponse(BaseModel):
    total_agents: int
    total_messages: int
    inbound_count: int
    outbound_count: int
    unread_messages: int
    pending_reviews: int
    threats_neutralized: int
    active_websockets: int

