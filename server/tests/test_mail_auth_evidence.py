import datetime

import pytest

from app.models import Message
from app.schemas import MessageResponse
from app.services import mail_auth


def test_dkim_evidence_keeps_each_signature_result(monkeypatch):
    class FakeDKIM:
        def __init__(self, _raw, timeout):
            assert timeout == mail_auth.DKIM_QUERY_TIMEOUT_SECONDS

        def verify(self, idx):
            return idx == 1

    monkeypatch.setattr(mail_auth.dkim, "DKIM", FakeDKIM)
    raw = (
        b"From: sender@example.com\r\n"
        b"DKIM-Signature: v=1; d=first.example; s=a; b=x\r\n"
        b"DKIM-Signature: v=1; d=second.example; s=b; b=y\r\n\r\nbody"
    )

    status, passing_domains, signatures = mail_auth._check_dkim(raw)

    assert status == "pass"
    assert passing_domains == ["second.example"]
    assert signatures == [
        {"status": "fail", "aligned": None, "detail": "Signature did not verify", "domain": "first.example", "selector": "a"},
        {"status": "pass", "aligned": None, "detail": "Signature verified", "domain": "second.example", "selector": "b"},
    ]


def test_dmarc_exposes_flattened_policy_and_per_signature_alignment(monkeypatch):
    monkeypatch.setattr(
        mail_auth,
        "_dmarc_tree",
        lambda domain, _deadline: [("example.com", "v=DMARC1; p=reject; adkim=s", {"p": "reject", "adkim": "s"})]
        if domain in {"example.com", "mail.example.com"}
        else [(domain, "v=DMARC1; p=none", {"p": "none"})],
    )
    details = {}
    signatures = [
        {"status": "pass", "domain": "example.com", "selector": "s1"},
        {"status": "pass", "domain": "example.com", "selector": "s2"},
    ]
    result = mail_auth._check_dmarc(
        b"From: sender@example.com\r\n\r\nbody", "bounce@other.net", "mx.other.net",
        "fail", "pass", ["example.com", "example.com"], details,
        signatures,
    )
    assert result == "pass"
    assert details["policy_domain"] == "example.com"
    assert details["domain"] == "example.com"
    assert details["effective_policy"] == details["p"] == "reject"
    assert details["policy_value"] == "reject"
    assert details["adkim"] == "s" and details["aligned"] is True
    assert details["aligned_by"] == ["dkim"]
    assert details["policy"]["effective"] == "reject"
    assert all(signature["aligned"] is True and "Aligned" in signature["detail"] for signature in signatures)
    assert details["alignment"]["dkim"] == [
        {"domain": "example.com", "selector": "s1", "aligned": True, "mode": "s", "policy_domain": "example.com"},
        {"domain": "example.com", "selector": "s2", "aligned": True, "mode": "s", "policy_domain": "example.com"},
    ]


def test_message_authentication_supports_legacy_and_structured_evidence():
    message = Message(spf_status="pass", dkim_status="pass", dmarc_status="pass")
    message.auth_evidence = {
        "spf": {"status": "pass", "identity": "sender@example.com"},
        "dkim": {"status": "pass", "passing_domains": ["example.com"]},
        "dmarc": {"status": "pass"},
    }

    assert message.authentication == {
        "spf": {"status": "pass", "identity": "sender@example.com"},
        "dkim": [],
        "dmarc": {"status": "pass"},
    }

    message.auth_evidence["authentication"] = {
        "spf": {"status": "pass"},
        "dkim": [{"status": "pass", "domain": "example.com"}],
        "dmarc": {"status": "pass", "policy": {"p": "reject"}, "alignment": {"dkim": True}},
    }
    message.id = "message-1"
    message.agent_email = "agent@example.com"
    message.direction = "inbound"
    message.is_read = False
    message.sender = "sender@example.com"
    message.recipient = "agent@example.com"
    message.subject = "Subject"
    message.body_text = "Body"
    message.body_html = None
    message.thread_id = "thread-1"
    message.raw_message_id = None
    message.in_reply_to = None
    message.references = None
    message.rfc_message_id = "<message-1@example.com>"
    message.verified_domain = None
    message.status = "delivered"
    message.labels = []
    message.threat_score = 0
    message.threat_details = ""
    message.created_at = datetime.datetime.now(datetime.timezone.utc)
    response = MessageResponse.model_validate(message, from_attributes=True)
    assert response.authentication["dkim"] == [{"status": "pass", "domain": "example.com"}]


@pytest.mark.parametrize(
    ("dmarc_status", "policy", "expected"),
    [
        ("fail", {"p": "reject", "effective": "reject"}, "reject"),
        ("fail", {"p": "none", "sp": "quarantine", "effective": "quarantine"}, "quarantine"),
        ("none", {"p": "reject", "effective": "reject"}, None),
        ("temperror", {"p": "reject", "effective": "reject"}, None),
        ("unverified", {"p": "reject", "effective": "reject"}, None),
        ("fail", {"p": "none", "effective": "none"}, None),
    ],
)
def test_dmarc_disposition_only_applies_to_exact_failure(dmarc_status, policy, expected):
    evidence = {"dmarc": {"status": dmarc_status, "policy": policy}}
    assert mail_auth.inbound_dmarc_disposition(dmarc_status, evidence) == expected


def test_dmarc_inherited_subdomain_policy_uses_sp(monkeypatch):
    monkeypatch.setattr(
        mail_auth,
        "_dmarc_tree",
        lambda domain, _deadline: (
            [("example.com", "v=DMARC1; p=none; sp=reject", {"p": "none", "sp": "reject"})]
            if domain == "sub.example.com"
            else [(domain, "v=DMARC1; p=none", {"p": "none"})]
        ),
    )
    details = {}
    result = mail_auth._check_dmarc(
        b"From: sender@sub.example.com\r\n\r\nbody", "bounce@other.net", "mx.other.net",
        "fail", "none", [], details,
    )
    assert result == "fail"
    assert details["policy"] == {"domain": "example.com", "p": "none", "sp": "reject", "effective": "reject"}

