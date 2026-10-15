import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Domain
from app.services import domain_dns
from app.services.email_service import EmailService, _send_smtp_sync
from app.services.smtp_secrets import decrypt_secret
from tests.conftest import ADMIN, make_agent, make_key

D = "mail.example.com"


class FakeDNS:
    def __init__(self):
        self.txt, self.mx = {}, {}
        self.fail = False

    def resolve_txt(self, name):
        return None if self.fail else self.txt.get(name, [])

    def resolve_mx(self, name):
        return None if self.fail else self.mx.get(name, [])


@pytest.fixture
def dns(monkeypatch):
    fake = FakeDNS()
    monkeypatch.setattr(domain_dns, "_resolve_txt", fake.resolve_txt)
    monkeypatch.setattr(domain_dns, "_resolve_mx", fake.resolve_mx)
    return fake


def publish(fake, rec, domain=D):
    fake.mx[domain] = [rec["mx_record"].split()[1]]
    fake.txt[domain] = [rec["spf_record"]]
    fake.txt[f"{rec['dkim_selector']}._domainkey.{domain}"] = [rec["dkim_record"]]
    fake.txt[f"_dmarc.{domain}"] = [rec["dmarc_record"]]


async def register(client, name=D):
    r = await client.post("/v1/domains", headers=ADMIN, json={"domain": name})
    assert r.status_code == 201, r.text
    return r.json()


async def test_register_generates_real_key_and_never_leaks_it(client):
    rec = await register(client)
    assert rec["dkim_record"].startswith("v=DKIM1; k=rsa; p=MIIB") and "..." not in rec["dkim_record"]
    assert rec["dkim_selector"] == "oae1" and "private" not in str(rec).lower()
    async with AsyncSessionLocal() as db:
        stored = (await db.scalar(select(Domain))).dkim_private_key
    assert stored.startswith("fernet:v1:") and "BEGIN" not in stored
    assert "BEGIN RSA PRIVATE KEY" in decrypt_secret(stored)


@pytest.mark.parametrize("bad", ["nodots", "-bad.example.com", "a..b.com", "exa mple.com", "x" * 64 + ".com", "http://a.com"])
async def test_invalid_domains_rejected(client, bad):
    assert (await client.post("/v1/domains", headers=ADMIN, json={"domain": bad})).status_code == 400


async def test_all_records_published_verifies(client, dns):
    rec = await register(client)
    publish(dns, rec)
    r = (await client.post(f"/v1/domains/{D}/verify", headers=ADMIN)).json()
    assert (r["mx_status"], r["spf_status"], r["dkim_status"], r["dmarc_status"], r["status"]) == (
        "verified", "verified", "verified", "verified", "verified")
    assert r["last_checked_at"]


async def test_nothing_published_stays_pending(client, dns):
    await register(client)
    r = (await client.post(f"/v1/domains/{D}/verify", headers=ADMIN)).json()
    assert r["status"] == "pending_dns" and r["mx_status"] == r["dkim_status"] == "pending"


async def test_wrong_values_are_mismatch_not_verified(client, dns):
    rec = await register(client)
    publish(dns, rec)
    dns.mx[D] = ["other.host.net"]
    dns.txt[f"oae1._domainkey.{D}"] = ["v=DKIM1; k=rsa; p=AAAA"]
    dns.txt[D] = [rec["spf_record"], "v=spf1 -all"]  # two SPF records is a permanent error
    r = (await client.post(f"/v1/domains/{D}/verify", headers=ADMIN)).json()
    assert (r["mx_status"], r["dkim_status"], r["spf_status"], r["status"]) == ("mismatch", "mismatch", "mismatch", "pending_dns")


async def test_dns_failure_is_error_and_does_not_verify(client, dns):
    rec = await register(client)
    publish(dns, rec)
    dns.fail = True
    r = (await client.post(f"/v1/domains/{D}/verify", headers=ADMIN)).json()
    assert r["status"] == "pending_dns" and r["mx_status"] == "error"


async def test_dmarc_optional_for_verified(client, dns):
    rec = await register(client)
    publish(dns, rec)
    del dns.txt[f"_dmarc.{D}"]
    r = (await client.post(f"/v1/domains/{D}/verify", headers=ADMIN)).json()
    assert r["status"] == "verified" and r["dmarc_status"] == "pending"


async def test_spf_include_setting(client, dns, monkeypatch):
    monkeypatch.setattr(domain_dns.settings, "SPF_INCLUDE", "amazonses.com")
    rec = await register(client)
    assert rec["spf_record"] == "v=spf1 mx include:amazonses.com ~all"
    publish(dns, rec)
    dns.txt[D] = ["v=spf1 mx ~all"]  # missing the relay include
    assert (await client.post(f"/v1/domains/{D}/verify", headers=ADMIN)).json()["spf_status"] == "mismatch"


async def test_legacy_placeholder_domain_cannot_be_verified(client, dns):
    async with AsyncSessionLocal() as db:
        db.add(Domain(id="dom_old", domain="old.example.com", mx_record="10 mx", spf_record="v=spf1", dkim_record="p=...",
                      dmarc_record="v=DMARC1"))
        await db.commit()
    assert (await client.post("/v1/domains/old.example.com/verify", headers=ADMIN)).status_code == 409


async def test_domains_admin_only(client):
    await make_agent(client, "a@agents.local")
    key, _ = await make_key(client, "a@agents.local", "full_access")
    assert (await client.post("/v1/domains", headers=key, json={"domain": D})).status_code == 403
    assert (await client.post(f"/v1/domains/{D}/verify", headers=key)).status_code == 403


async def test_registration_refused_without_encryption_key(client, monkeypatch):
    monkeypatch.setattr("app.services.smtp_secrets.settings.SMTP_CONFIG_ENCRYPTION_KEY", "")
    r = await client.post("/v1/domains", headers=ADMIN, json={"domain": D})
    assert r.status_code == 503


async def test_signing_params_only_for_verified_domain(client, dns):
    rec = await register(client)
    async with AsyncSessionLocal() as db:
        assert await EmailService._dkim_signing_for(db, f"bot@{D}") is None  # pending
    publish(dns, rec)
    await client.post(f"/v1/domains/{D}/verify", headers=ADMIN)
    async with AsyncSessionLocal() as db:
        params = await EmailService._dkim_signing_for(db, f"bot@{D}")
        assert params["domain"] == D and params["selector"] == "oae1" and "BEGIN" in params["private_key"]
        assert await EmailService._dkim_signing_for(db, "bot@other.org") is None


async def test_signed_message_verifies_against_published_key(client, dns, monkeypatch):
    import dkim, smtplib
    rec = await register(client)
    publish(dns, rec)
    await client.post(f"/v1/domains/{D}/verify", headers=ADMIN)
    async with AsyncSessionLocal() as db:
        params = await EmailService._dkim_signing_for(db, f"bot@{D}")

    sent = {}

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def starttls(self): pass
        def login(self, *a): pass
        def sendmail(self, frm, to, data):
            sent["data"] = data
            return {}
        def quit(self): pass

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    ok, _ = _send_smtp_sync("relay", 587, "", "", False, False, f"bot@{D}", "to@example.net", "Hi", "body",
                            custom_msg_id="<x@mail.example.com>", dkim_signing=params)
    assert ok
    raw = sent["data"].encode() if isinstance(sent["data"], str) else sent["data"]
    assert raw.startswith(b"DKIM-Signature:")
    public = rec["dkim_record"]
    published = lambda name, timeout=5: public.encode()  # noqa: E731
    assert dkim.verify(raw, dnsfunc=published)
    tampered = raw.replace(b"Ym9keQ==", b"ZXZpbCE=")  # base64 of "body" -> "evil!"
    assert tampered != raw and not dkim.verify(tampered, dnsfunc=published)


async def test_configured_signing_failure_refuses_smtp_submission(monkeypatch):
    import smtplib
    sent = {}

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def starttls(self): pass
        def sendmail(self, frm, to, data):
            sent["data"] = data
            return {}
        def quit(self): pass

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    ok, info = _send_smtp_sync("relay", 587, "", "", False, False, "bot@x.com", "t@y.net", "s", "b",
                            dkim_signing={"domain": "x.com", "selector": "s", "private_key": "not a key"})
    assert not ok and "Configured DKIM signing failed" in info
    assert "data" not in sent


async def test_signature_survives_a_relay_that_rewrites_message_id_and_date(client, dns, monkeypatch):
    """Regression for a live failure: SES replaces Message-ID and reformats Date after we sign."""
    import re, smtplib, dkim
    rec = await register(client)
    publish(dns, rec)
    await client.post(f"/v1/domains/{D}/verify", headers=ADMIN)
    async with AsyncSessionLocal() as db:
        params = await EmailService._dkim_signing_for(db, f"bot@{D}")
    sent = {}

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def starttls(self): pass
        def login(self, *a): pass
        def sendmail(self, frm, to, data):
            sent["data"] = data
            return {}
        def quit(self): pass

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    ok, _ = _send_smtp_sync("relay", 587, "", "", False, False, f"bot@{D}", "to@example.net", "Hi", "body",
                            custom_msg_id="<original@mail.example.com>", dkim_signing=params)
    assert ok
    raw = sent["data"].encode() if isinstance(sent["data"], str) else sent["data"]
    public = rec["dkim_record"]
    published = lambda name, timeout=5: public.encode()  # noqa: E731
    assert dkim.verify(raw, dnsfunc=published)

    rewritten = re.sub(rb"(?im)^Message-ID:.*$", b"Message-ID: <010f01a1-relay@us-east-2.amazonses.com>", raw)
    rewritten = re.sub(rb"(?im)^Date:.*$", b"Date: Wed, 14 Oct 2026 23:26:19 +0000", rewritten)
    assert rewritten != raw and dkim.verify(rewritten, dnsfunc=published)

    # headers that ARE signed must still be protected
    tampered = re.sub(rb"(?im)^Subject:.*$", b"Subject: Changed", raw)
    assert not dkim.verify(tampered, dnsfunc=published)
    h_tag = re.search(rb"h=([^;]+);", raw.split(b"\r\n\r\n")[0].replace(b"\r\n ", b"").replace(b"\n ", b"")).group(1).lower()
    assert b"message-id" not in h_tag and b"date" not in h_tag
