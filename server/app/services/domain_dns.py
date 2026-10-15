"""Custom-domain DNS records: expected values, DKIM key generation and bounded verification."""

import base64
import re
from dataclasses import dataclass

import dns.exception
import dns.resolver
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import settings

DNS_TIMEOUT_SECONDS = 3.0
DKIM_SELECTOR = "oae1"
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
DOMAIN_RE = re.compile(rf"^(?:{_LABEL}\.)+{_LABEL}$")


def valid_domain(value: str) -> bool:
    return len(value) <= 253 and bool(DOMAIN_RE.fullmatch(value))


def mail_hostname() -> str:
    return (settings.MAIL_HOSTNAME.strip().lower().rstrip(".")
            or f"mx.{settings.SHARED_DOMAIN.strip().lower().rstrip('.')}")


def generate_dkim_keypair() -> tuple[str, str]:
    """Return (private key PEM, base64 DER public key) for a new 2048-bit RSA DKIM key."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
    ).decode("ascii")
    public_der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return private_pem, base64.b64encode(public_der).decode("ascii")


@dataclass(frozen=True)
class ExpectedRecords:
    mx: str
    spf: str
    dkim: str
    dmarc: str


def expected_records(domain: str, public_key_b64: str) -> ExpectedRecords:
    include = settings.SPF_INCLUDE.strip().lower()
    spf = "v=spf1 mx" + (f" include:{include}" if include else "") + " ~all"
    return ExpectedRecords(
        mx=f"10 {mail_hostname()}",
        spf=spf,
        dkim=f"v=DKIM1; k=rsa; p={public_key_b64}",
        dmarc=f"v=DMARC1; p=none; rua=mailto:dmarc@{domain}",
    )


def _resolver() -> dns.resolver.Resolver:
    resolver = dns.resolver.Resolver()
    resolver.lifetime = DNS_TIMEOUT_SECONDS
    resolver.timeout = DNS_TIMEOUT_SECONDS
    return resolver


def _resolve_txt(name: str) -> list[str] | None:
    """TXT strings (joined per record), [] when absent, None on a transient DNS failure."""
    try:
        answer = _resolver().resolve(name, "TXT")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        return []
    except dns.exception.DNSException:
        return None
    return ["".join(part.decode("utf-8", "replace") for part in rdata.strings) for rdata in answer]


def _resolve_mx(name: str) -> list[str] | None:
    try:
        answer = _resolver().resolve(name, "MX")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        return []
    except dns.exception.DNSException:
        return None
    return [str(rdata.exchange).lower().rstrip(".") for rdata in answer]


def check_mx(domain: str) -> str:
    hosts = _resolve_mx(domain)
    if hosts is None:
        return "error"
    if not hosts:
        return "pending"
    return "verified" if mail_hostname() in hosts else "mismatch"


def check_spf(domain: str) -> str:
    records = _resolve_txt(domain)
    if records is None:
        return "error"
    spf = [r for r in records if r.lower().startswith("v=spf1")]
    if not spf:
        return "pending"
    if len(spf) > 1:
        return "mismatch"  # multiple SPF records are a permanent error for receivers
    terms = spf[0].lower().split()
    include = settings.SPF_INCLUDE.strip().lower()
    if include:
        return "verified" if f"include:{include}" in terms else "mismatch"
    return "verified" if "mx" in terms else "mismatch"


def _tags(record: str) -> dict[str, str]:
    out = {}
    for part in record.split(";"):
        key, sep, value = part.strip().partition("=")
        if sep:
            out[key.strip().lower()] = value.strip()
    return out


def check_dkim(domain: str, selector: str, public_key_b64: str) -> str:
    records = _resolve_txt(f"{selector}._domainkey.{domain}")
    if records is None:
        return "error"
    candidates = [r for r in records if "p=" in r.replace(" ", "").lower()]
    if not candidates:
        return "pending"
    wanted = "".join(public_key_b64.split())
    for record in candidates:
        if "".join(_tags(record).get("p", "").split()) == wanted:
            return "verified"
    return "mismatch"


def check_dmarc(domain: str) -> str:
    records = _resolve_txt(f"_dmarc.{domain}")
    if records is None:
        return "error"
    found = [r for r in records if r.lower().replace(" ", "").startswith("v=dmarc1")]
    return "verified" if found else "pending"


def overall_status(mx: str, spf: str, dkim: str) -> str:
    """DMARC is recommended but not required; mail flow needs MX, SPF and DKIM."""
    return "verified" if mx == spf == dkim == "verified" else "pending_dns"
