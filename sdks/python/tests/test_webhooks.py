"""Webhook verification: golden fixtures shared with the server and TypeScript tests."""

import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest

from open_agent_email import (
    WebhookSignatureError,
    construct_event,
    sign_webhook,
    sign_webhook_v1,
    verify_webhook_signature,
)

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "webhook_signatures.json"
CASES = json.loads(FIXTURE.read_text())["cases"]
IDS = [c["name"] for c in CASES]
ROTATION = next(c for c in CASES if len(c["secrets"]) == 2)


def _body(case):
    return case["body"].encode()


def _now(case):
    return int(case["timestamp"])


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_signing_helpers_reproduce_fixture(case):
    ts, body = case["timestamp"], _body(case)
    v1 = ",".join(f"v1={sign_webhook_v1(s, ts, body)}" for s in case["secrets"])
    assert f"t={ts},{v1}" == case["expected_headers"]["Webhook-Signature"]
    legacy = ",".join(sign_webhook(s, ts, case["event_id"], body) for s in case["secrets"])
    assert legacy == case["expected_headers"]["X-Webhook-Signature"]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_fixture_matches_plain_hmac(case):
    mac = hmac.new(case["secrets"][0].encode(), case["timestamp"].encode() + b"." + _body(case), hashlib.sha256)
    assert f"v1={mac.hexdigest()}" in case["expected_headers"]["Webhook-Signature"]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_full_header_set_verifies_for_every_secret(case):
    for secret in case["secrets"]:
        event = construct_event(_body(case), case["expected_headers"], secret, now=_now(case))
        assert event["id"] == case["event_id"]
        assert event["type"] == case["event_type"]
        assert event["timestamp"] == _now(case)
        assert event["payload"]["type"] == "email.received"


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_either_header_set_alone_is_enough(case):
    headers = case["expected_headers"]
    legacy_only = {k: v for k, v in headers.items() if not k.startswith("Webhook-")}
    current_only = {"Webhook-Signature": headers["Webhook-Signature"]}
    for subset in (legacy_only, current_only):
        assert construct_event(_body(case), subset, case["secrets"][0], now=_now(case))["id"] == case["event_id"]


def test_current_header_is_preferred_over_a_bad_legacy_header():
    case = CASES[0]
    headers = {**case["expected_headers"], "X-Webhook-Signature": "sha256=" + "0" * 64}
    assert construct_event(_body(case), headers, case["secrets"][0], now=_now(case))


def test_present_but_invalid_current_header_is_an_error_even_with_valid_legacy():
    case = CASES[0]
    headers = {**case["expected_headers"], "Webhook-Signature": f"t={case['timestamp']},v1={'0' * 64}"}
    with pytest.raises(WebhookSignatureError, match="does not match"):
        construct_event(_body(case), headers, case["secrets"][0], now=_now(case))


def test_rejects_tampering_wrong_secret_and_moved_timestamp():
    case = CASES[0]
    only = {"Webhook-Signature": case["expected_headers"]["Webhook-Signature"]}
    with pytest.raises(WebhookSignatureError):
        construct_event(_body(case).replace(b"msg_abc", b"msg_xyz"), only, case["secrets"][0], now=_now(case))
    with pytest.raises(WebhookSignatureError, match="does not match"):
        construct_event(_body(case), only, "whsec_other", now=_now(case))
    moved = {"Webhook-Signature": only["Webhook-Signature"].replace(f"t={case['timestamp']}", f"t={_now(case) + 1}")}
    with pytest.raises(WebhookSignatureError, match="does not match"):
        construct_event(_body(case), moved, case["secrets"][0], now=_now(case))


@pytest.mark.parametrize("scheme", ["current", "legacy"])
def test_five_minute_window_edge_inclusive(scheme):
    case = CASES[0]
    headers = case["expected_headers"]
    subset = ({"Webhook-Signature": headers["Webhook-Signature"]} if scheme == "current"
              else {k: v for k, v in headers.items() if not k.startswith("Webhook-")})
    assert construct_event(_body(case), subset, case["secrets"][0], now=_now(case) + 300)
    for offset in (301, -301):
        with pytest.raises(WebhookSignatureError, match="window"):
            construct_event(_body(case), subset, case["secrets"][0], now=_now(case) + offset)


def test_dual_secret_rotation_accepts_either_secret_or_a_list():
    only = {"Webhook-Signature": ROTATION["expected_headers"]["Webhook-Signature"]}
    for secret in ROTATION["secrets"]:
        assert construct_event(_body(ROTATION), only, secret, now=_now(ROTATION))
    assert construct_event(_body(ROTATION), only, ["whsec_unrelated", ROTATION["secrets"][1]], now=_now(ROTATION))
    with pytest.raises(WebhookSignatureError, match="does not match"):
        construct_event(_body(ROTATION), only, ["whsec_unrelated"], now=_now(ROTATION))


@pytest.mark.parametrize("value", ["", "garbage", "t=abc,v1=00", "v1=" + "a" * 64, "t=1800000000",
                                   "t=1800000000,v2=" + "a" * 64])
def test_malformed_current_header_rejected(value):
    case = CASES[0]
    with pytest.raises(WebhookSignatureError):
        construct_event(_body(case), {"Webhook-Signature": value}, case["secrets"][0], now=_now(case))


def test_unknown_header_keys_are_ignored():
    case = CASES[0]
    v1 = case["expected_headers"]["Webhook-Signature"].split(",")[1]
    headers = {"Webhook-Signature": f"t={case['timestamp']},x=1,{v1},future=ok"}
    assert construct_event(_body(case), headers, case["secrets"][0], now=_now(case))


def test_header_names_are_case_insensitive_and_body_supplies_id_and_type():
    case = CASES[0]
    headers = {"webhook-signature": case["expected_headers"]["Webhook-Signature"]}
    event = construct_event(_body(case), headers, case["secrets"][0], now=_now(case))
    assert event["id"] == case["event_id"] and event["type"] == "email.received"


def test_input_validation():
    case = CASES[0]
    headers = case["expected_headers"]
    with pytest.raises(WebhookSignatureError, match="No signing secret"):
        construct_event(_body(case), headers, [], now=_now(case))
    with pytest.raises(WebhookSignatureError, match="No signing secret"):
        construct_event(_body(case), headers, "", now=_now(case))
    with pytest.raises(WebhookSignatureError, match="exact request bytes"):
        construct_event(case["body"], headers, case["secrets"][0], now=_now(case))  # str, not bytes
    with pytest.raises(WebhookSignatureError, match="Missing"):
        construct_event(_body(case), {}, case["secrets"][0], now=_now(case))
    bad = b"not json"
    ts = case["timestamp"]
    signed = {"Webhook-Signature": f"t={ts},v1={sign_webhook_v1(case['secrets'][0], ts, bad)}"}
    with pytest.raises(WebhookSignatureError, match="valid JSON"):
        construct_event(bad, signed, case["secrets"][0], now=_now(case))


def test_uses_wall_clock_by_default():
    secret, body = "whsec_clock", b'{"id":"evt_clock","type":"webhook.test"}'
    ts = str(int(time.time()))
    headers = {"Webhook-Signature": f"t={ts},v1={sign_webhook_v1(secret, ts, body)}"}
    assert construct_event(body, headers, secret)["type"] == "webhook.test"
    stale = str(int(time.time()) - 3600)
    old = {"Webhook-Signature": f"t={stale},v1={sign_webhook_v1(secret, stale, body)}"}
    with pytest.raises(WebhookSignatureError, match="window"):
        construct_event(body, old, secret)


def test_existing_legacy_verifier_still_works_and_agrees_with_sign_webhook():
    secret, body, event_id = "whsec_legacy", b'{"id":"evt_legacy"}', "evt_legacy"
    ts = str(int(time.time()))
    signature = sign_webhook(secret, ts, event_id, body)
    assert verify_webhook_signature(body, event_id=event_id, timestamp=ts, signature=signature, secret=secret)
    assert not verify_webhook_signature(body + b" ", event_id=event_id, timestamp=ts, signature=signature, secret=secret)
    stale = str(int(time.time()) - 3600)
    old = sign_webhook(secret, stale, event_id, body)
    assert not verify_webhook_signature(body, event_id=event_id, timestamp=stale, signature=old, secret=secret)
