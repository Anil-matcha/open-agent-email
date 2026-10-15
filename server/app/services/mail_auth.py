"""Bounded SPF and DKIM checks for messages received by the SMTP listener."""

import asyncio
import datetime
import email
from email import policy
from email.utils import getaddresses
import logging
import re
import time

import dkim
import dns.exception
import dns.resolver
import spf
from checkdmarc.dmarc import parse_dmarc_record

logger = logging.getLogger(__name__)

SPF_QUERY_TIMEOUT_SECONDS = 8
DKIM_QUERY_TIMEOUT_SECONDS = 5
MAX_DKIM_SIGNATURES = 5
DMARC_DNS_TIMEOUT_SECONDS = 1.5
DMARC_TOTAL_TIMEOUT_SECONDS = 8
DMARC_RELEVANT_TAGS = {"p", "sp", "np", "psd", "adkim", "aspf"}


def _check_spf(peer_ip: str | None, mail_from: str, helo_domain: str) -> str:
    if not peer_ip or (not mail_from and not helo_domain):
        return "unverified"
    try:
        result, _explanation = spf.check2(
            i=peer_ip,
            s=mail_from,
            h=helo_domain,
            querytime=SPF_QUERY_TIMEOUT_SECONDS,
        )
        return result if result in {
            "pass", "fail", "softfail", "neutral", "none", "permerror", "temperror"
        } else "unverified"
    except Exception:
        logger.exception("SPF evaluation failed unexpectedly")
        return "unverified"


def _check_dkim(raw_message: bytes) -> tuple[str, list[str], list[dict[str, str]]]:
    message = email.message_from_bytes(raw_message, policy=policy.default)
    signatures = message.get_all("DKIM-Signature", [])
    signature_count = len(signatures)
    if signature_count == 0:
        return "none", [], []

    evidence: list[dict[str, str]] = []
    for signature in signatures[:MAX_DKIM_SIGNATURES]:
        fields = {}
        for name in ("d", "s", "a", "c"):
            value = re.search(rf"(?:^|;)\s*{name}\s*=\s*([^;\s]+)", str(signature), re.IGNORECASE)
            if value:
                key = {"d": "domain", "s": "selector", "a": "algorithm", "c": "canonicalization"}[name]
                normalized = value.group(1).strip().rstrip(".").lower() if name in {"d", "s"} else value.group(1).strip().lower()
                fields[key] = normalized
        evidence.append({"status": "unverified", "aligned": None, "detail": None, **fields})

    try:
        verifier = dkim.DKIM(raw_message, timeout=DKIM_QUERY_TIMEOUT_SECONDS)
    except dkim.DKIMException:
        return "fail", [], [{**item, "status": "fail", "detail": "Malformed DKIM signature"} for item in evidence]
    except Exception:
        logger.exception("Could not initialize DKIM verification")
        return "unverified", [], evidence

    passing_domains = []
    for index in range(min(signature_count, MAX_DKIM_SIGNATURES)):
        try:
            if verifier.verify(idx=index):
                evidence[index]["status"] = "pass"
                evidence[index]["detail"] = "Signature verified"
                signing_domain = evidence[index].get("domain")
                if signing_domain:
                    passing_domains.append(signing_domain)
            else:
                evidence[index]["status"] = "fail"
                evidence[index]["detail"] = "Signature did not verify"
        except dns.exception.Timeout:
            evidence[index]["status"] = "temperror"
            evidence[index]["detail"] = "DNS lookup timed out while checking the signing key"
            return "temperror", passing_domains, evidence
        except dns.resolver.NoNameservers:
            evidence[index]["status"] = "temperror"
            evidence[index]["detail"] = "No DNS nameserver could answer the signing key lookup"
            return "temperror", passing_domains, evidence
        except dkim.DKIMException:
            # A malformed signature or invalid key fails this signature.
            logger.info("DKIM signature %d did not verify", index)
            evidence[index]["status"] = "fail"
            evidence[index]["detail"] = "Signature did not verify"
        except Exception:
            logger.exception("Unexpected error during DKIM verification")
            evidence[index]["status"] = "unverified"
            evidence[index]["detail"] = "Verification could not be completed"
            return "unverified", passing_domains, evidence

    # If signatures remain unchecked, a valid signature may be among them.
    if passing_domains:
        return "pass", passing_domains, evidence
    return ("unverified", [], evidence) if signature_count > MAX_DKIM_SIGNATURES else ("fail", [], evidence)


def _get_record_tags(record: str, domain: str) -> dict[str, str]:
    """Parse only the DMARC tags used for policy/alignment, without report DNS lookups."""
    relevant_parts = []
    for part in record.split(";")[1:]:
        match = re.fullmatch(r"\s*([A-Za-z]+)\s*=\s*(.*?)\s*", part)
        if match and match.group(1).lower() in DMARC_RELEVANT_TAGS:
            relevant_parts.append(f"{match.group(1)}={match.group(2)}")
    sanitized_record = "v=DMARC1" + (";" + ";".join(relevant_parts) if relevant_parts else "")
    parsed = parse_dmarc_record(sanitized_record, domain, timeout=DMARC_DNS_TIMEOUT_SECONDS, retries=0)
    return {tag: str(value["value"]).lower() for tag, value in parsed["tags"].items()}


class DMARCDNSBudgetExceeded(Exception):
    pass


def _direct_dmarc_record(domain: str, deadline: float) -> tuple[str, dict[str, str]] | None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DMARCDNSBudgetExceeded
    lookup_timeout = min(DMARC_DNS_TIMEOUT_SECONDS, remaining)
    resolver = dns.resolver.Resolver()
    resolver.timeout = lookup_timeout
    resolver.lifetime = lookup_timeout
    try:
        answers = resolver.resolve(f"_dmarc.{domain}", "TXT", search=False)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return None
    except dns.exception.DNSException:
        raise

    records = []
    for answer in answers:
        chunks = getattr(answer, "strings", None)
        raw_record = b"".join(chunks).decode("utf-8", errors="replace") if chunks else answer.to_text().strip('"')
        if re.match(r"^[vV]\s*=\s*DMARC1(?:\s*;|$)", raw_record):
            records.append(raw_record)
    # RFC 9989 says multiple DMARC records at one name are all discarded.
    if len(records) != 1:
        return None
    try:
        return records[0], _get_record_tags(records[0], domain)
    except Exception:
        logger.info("Ignoring invalid DMARC record at %s", domain)
        return None


def _dmarc_tree(domain: str, deadline: float) -> list[tuple[str, str, dict[str, str]]]:
    """Return DMARC records on a bounded RFC 9989 tree walk for a domain."""
    labels = domain.split(".")
    targets = [domain]
    # The initial full-name query plus at most seven parent queries bounds a
    # maliciously deep From/signing domain to the RFC's eight-query limit.
    first_parent = 1 if len(labels) <= 8 else len(labels) - 7
    targets.extend(".".join(labels[index:]) for index in range(first_parent, len(labels)))
    records: list[tuple[str, str, dict[str, str]]] = []
    for target in targets[:8]:
        found = _direct_dmarc_record(target, deadline)
        if found:
            record, tags = found
            records.append((target, record, tags))
            if tags.get("psd") in {"n", "y"}:
                break
    return records


def _organizational_domain(domain: str, records: list[tuple[str, str, dict[str, str]]]) -> str:
    if not records:
        return domain
    for location, _record, tags in records:
        if tags.get("psd") == "n":
            return location
        if tags.get("psd") == "y":
            domain_labels = domain.split(".")
            psd_labels = location.split(".")
            if len(domain_labels) > len(psd_labels) and domain_labels[-len(psd_labels):] == psd_labels:
                return ".".join(domain_labels[-len(psd_labels) - 1:])
            return domain
    return min((location for location, _record, _tags in records), key=lambda name: len(name.split(".")))


def _check_dmarc(raw_message: bytes, mail_from: str, helo_domain: str, spf_status: str, dkim_status: str, dkim_domains: list[str], details: dict | None = None, dkim_signatures: list[dict] | None = None) -> str:
    if details is not None:
        details.update({
            "policy": None,
            "policy_domain": None,
            "domain": None,
            "policy_value": None,
            "effective_policy": None,
            "aligned": None,
            "spf_aligned": None,
            "dkim_aligned": [],
            "aligned_by": [],
            "p": None,
            "sp": None,
            "adkim": None,
            "aspf": None,
            "alignment": {"spf": None, "dkim": []},
        })
    message = email.message_from_bytes(raw_message, policy=policy.default)
    from_headers = message.get_all("From", [])
    addresses = getaddresses([str(value) for value in from_headers])
    if len(from_headers) != 1 or len(addresses) != 1 or "@" not in addresses[0][1]:
        return "unverified"
    try:
        author_domain = _normalize_domain(addresses[0][1].rsplit("@", 1)[1])
    except ValueError:
        return "unverified"
    try:
        deadline = time.monotonic() + DMARC_TOTAL_TIMEOUT_SECONDS
        author_tree = _dmarc_tree(author_domain, deadline)
        exact_record = next((item for item in author_tree if item[0] == author_domain), None)
        policy_domain = author_domain
        if exact_record:
            policy_tags = exact_record[2]
        elif author_tree:
            policy_domain = _organizational_domain(author_domain, author_tree)
            policy_record = next((item for item in author_tree if item[0] == policy_domain), None)
            if not policy_record:
                # A PSD record applies when no record exists at its child org domain.
                policy_record = next((item for item in author_tree if item[2].get("psd") == "y"), None)
            if not policy_record:
                return "none"
            policy_domain = policy_record[0]
            policy_tags = policy_record[2]
        else:
            return "none"

        if details is not None:
            effective_policy = policy_tags.get("sp", policy_tags.get("p")) if author_domain != policy_domain else policy_tags.get("p")
            details["policy"] = {
                "domain": policy_domain,
                **{tag: policy_tags[tag] for tag in ("p", "sp", "np", "adkim", "aspf", "psd") if tag in policy_tags},
                "effective": effective_policy,
            }
            details.update({
                "policy_domain": policy_domain,
                "domain": policy_domain,
                "effective_policy": effective_policy,
                "policy_value": effective_policy,
                **{tag: policy_tags.get(tag) for tag in ("p", "sp", "adkim", "aspf")},
            })

        try:
            spf_domain = _normalize_domain(mail_from.rsplit("@", 1)[-1] if mail_from else helo_domain)
        except ValueError:
            spf_domain = ""
        spf_align_mode = policy_tags.get("aspf", "r")
        dkim_align_mode = policy_tags.get("adkim", "r")
        candidate_domains: list[tuple[str, str]] = []
        if spf_status == "pass" and spf_domain:
            candidate_domains.append(("spf", spf_domain))
        if dkim_status == "pass":
            candidate_domains.extend(("dkim", domain) for domain in dkim_domains)

        if not candidate_domains:
            if spf_status in {"unverified", "temperror"} or dkim_status in {"unverified", "temperror"}:
                return "unverified"
            if details is not None:
                details["aligned"] = False
                details["spf_aligned"] = None
                details["dkim_aligned"] = []
            return "fail"

        trees: dict[str, list[tuple[str, str, dict[str, str]]]] = {author_domain: author_tree}
        author_org = _organizational_domain(author_domain, author_tree)
        uncertain_alignment = False
        aligned_any = False
        used_dkim_signatures: dict[str, int] = {}
        aligned_by: list[str] = []

        def record_signature_alignment(identity_domain: str, aligned: bool) -> None:
            candidates = [item for item in (dkim_signatures or []) if item.get("domain") == identity_domain and item.get("status") == "pass"]
            sig_index = used_dkim_signatures.get(identity_domain, 0)
            sig = candidates[sig_index] if sig_index < len(candidates) else None
            used_dkim_signatures[identity_domain] = sig_index + 1
            if sig is not None:
                sig["aligned"] = aligned
                sig["detail"] = "Aligned with the visible From domain" if aligned else "Not aligned with the visible From domain"
            if details is not None:
                details["alignment"]["dkim"].append({
                    "domain": identity_domain,
                    "selector": sig.get("selector") if sig else None,
                    "aligned": aligned,
                    "mode": dkim_align_mode,
                    "policy_domain": policy_domain,
                })
        for mechanism, identity_domain in candidate_domains:
            try:
                identity_domain = _normalize_domain(identity_domain)
            except ValueError:
                uncertain_alignment = True
                continue
            if identity_domain == author_domain:
                aligned_any = True
                if details is not None:
                    if mechanism == "spf":
                        details["alignment"]["spf"] = True
                    else:
                        record_signature_alignment(identity_domain, True)
                if mechanism not in aligned_by:
                    aligned_by.append(mechanism)
                continue
            alignment_mode = spf_align_mode if mechanism == "spf" else dkim_align_mode
            if alignment_mode == "s":
                aligned = False
            else:
                if identity_domain not in trees:
                    trees[identity_domain] = _dmarc_tree(identity_domain, deadline)
                identity_org = _organizational_domain(identity_domain, trees[identity_domain])
                aligned = identity_org == author_org
            if details is not None:
                if mechanism == "spf":
                    details["alignment"]["spf"] = aligned
                else:
                    record_signature_alignment(identity_domain, aligned)
            if aligned:
                aligned_any = True
                if mechanism not in aligned_by:
                    aligned_by.append(mechanism)
            if alignment_mode != "s" and (not trees[identity_domain] or not author_tree):
                uncertain_alignment = True

        if aligned_any:
            if details is not None:
                details["aligned"] = True
                details["spf_aligned"] = details["alignment"]["spf"]
                details["dkim_aligned"] = [item["aligned"] for item in details["alignment"]["dkim"]]
                details["aligned_by"] = aligned_by
            return "pass"
        if uncertain_alignment:
            if details is not None:
                details["aligned"] = None
                details["spf_aligned"] = details["alignment"]["spf"]
                details["dkim_aligned"] = [item["aligned"] for item in details["alignment"]["dkim"]]
                details["aligned_by"] = aligned_by
            return "unverified"
        if spf_status in {"unverified", "temperror"} or dkim_status in {"unverified", "temperror"}:
            return "unverified"
        if details is not None:
            details["aligned"] = False
            details["spf_aligned"] = details["alignment"]["spf"]
            details["dkim_aligned"] = [item["aligned"] for item in details["alignment"]["dkim"]]
            details["aligned_by"] = []
        return "fail"
    except (dns.exception.DNSException, DMARCDNSBudgetExceeded):
        logger.info("DMARC DNS lookup was inconclusive for %s", author_domain)
        return "temperror"
    except Exception:
        logger.exception("DMARC evaluation failed unexpectedly")
        return "unverified"


def _normalize_domain(domain: str) -> str:
    normalized = domain.strip().rstrip(".").encode("idna").decode("ascii").lower()
    labels = normalized.split(".")
    if not normalized or len(normalized) > 253 or any(
        not label or len(label) > 63 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
        for label in labels
    ):
        raise ValueError("Invalid domain name")
    return normalized


async def evaluate_inbound_authentication(
    raw_message: bytes,
    peer_ip: str | None,
    mail_from: str,
    helo_domain: str,
) -> tuple[str, str, str, dict]:
    """Evaluate SPF, DKIM, and DMARC away from the asyncio SMTP event loop."""
    spf_result, dkim_result = await asyncio.gather(
        asyncio.to_thread(_check_spf, peer_ip, mail_from, helo_domain),
        asyncio.to_thread(_check_dkim, raw_message),
    )
    dkim_status, dkim_domains, dkim_signatures = dkim_result
    dmarc_details: dict = {}
    dmarc_result = await asyncio.to_thread(
        _check_dmarc, raw_message, mail_from, helo_domain, spf_result, dkim_status, dkim_domains,
        dmarc_details, dkim_signatures,
    )
    message = email.message_from_bytes(raw_message, policy=policy.default)
    from_values = [str(value) for value in message.get_all("From", [])]
    authentication = {
        "spf": {"status": spf_result, "identity": (mail_from or (f"postmaster@{helo_domain}" if helo_domain else ""))[:320] or None},
        "dkim": dkim_signatures,
        "dmarc": {"status": dmarc_result, **dmarc_details},
    }
    evidence = {
        "evaluated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "peer_ip": peer_ip[:64] if peer_ip else None,
        "smtp_mail_from": mail_from[:320],
        "helo_domain": helo_domain[:253],
        "from_header": [value[:512] for value in from_values[:1]],
        "spf": authentication["spf"],
        "dkim": {"status": dkim_status, "passing_domains": dkim_domains[:MAX_DKIM_SIGNATURES]},
        "dmarc": {"status": dmarc_result, **dmarc_details},
        "authentication": authentication,
    }
    return spf_result, dkim_status, dmarc_result, evidence


def inbound_dmarc_disposition(dmarc_status: str, auth_evidence: dict | None) -> str | None:
    """Return the published effective DMARC disposition for an exact failure.

    This intentionally consumes only the policy selected during the bounded DNS
    evaluation. Missing, temporary, malformed, and otherwise unverified evidence
    never becomes a delivery policy decision.
    """
    if dmarc_status != "fail" or not isinstance(auth_evidence, dict):
        return None
    dmarc = auth_evidence.get("dmarc")
    if not isinstance(dmarc, dict):
        authentication = auth_evidence.get("authentication")
        dmarc = authentication.get("dmarc") if isinstance(authentication, dict) else None
    policy_record = dmarc.get("policy") if isinstance(dmarc, dict) else None
    if not isinstance(policy_record, dict):
        return None
    effective = policy_record.get("effective")
    if effective not in {"reject", "quarantine", "none"}:
        # Compatibility for stored/older evidence, including inherited sp.
        from_domain = (auth_evidence.get("from_header") or [None])[0]
        policy_domain = policy_record.get("domain")
        inherited = bool(from_domain and policy_domain and str(from_domain).rsplit("@", 1)[-1].rstrip(".").lower() != str(policy_domain).lower())
        effective = policy_record.get("sp") if inherited and policy_record.get("sp") else policy_record.get("p")
    return effective if effective in {"reject", "quarantine"} else None
