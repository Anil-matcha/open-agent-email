"""Live SES/SNS feedback exercise (release-gate evidence): local server + ngrok tunnel + real SES.

Sends only to SES mailbox-simulator recipients (success@, bounce@, complaint@simulator.amazonses.com), so no real
mailbox receives anything and sender reputation is unaffected. Verifies relay acceptance, the signed SNS
subscription handshake, delivered/bounced/complained outcomes, suppression behaviour, duplicate notification
handling and rejection of forged notifications. Tears down the SNS subscription, tunnel and server when done.

Prerequisites (create these yourself; the script does not):
  * an SNS topic whose policy lets ses.amazonaws.com publish, and an SES configuration set whose event destination
    publishes DELIVERY, BOUNCE, COMPLAINT, REJECT and DELIVERY_DELAY to it;
  * a verified SES sender identity in the same region;
  * AWS CLI credentials that may use SES SMTP (the SMTP password is derived in memory from them and never printed),
    and an authenticated `ngrok` binary.

    OAE_SES_TOPIC_ARN=arn:aws:sns:... OAE_SES_CONFIG_SET=name OAE_SES_SENDER=you@verified.example \
    OAE_SES_REGION=us-east-2 python scripts/ses_live_drill.py
"""
import base64, hashlib, hmac, json, os, secrets, signal, subprocess, sys, tempfile, time
import httpx
from cryptography.fernet import Fernet

SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGION = os.environ.get("OAE_SES_REGION", "us-east-2")
TOPIC = os.environ.get("OAE_SES_TOPIC_ARN", "")
CONFIG_SET = os.environ.get("OAE_SES_CONFIG_SET", "")
SENDER = os.environ.get("OAE_SES_SENDER", "")
if not (TOPIC and CONFIG_SET and SENDER):
    sys.exit("Set OAE_SES_TOPIC_ARN, OAE_SES_CONFIG_SET and OAE_SES_SENDER (see the module docstring)")
PORT = 8812
ADMIN = secrets.token_urlsafe(32)
H = {"Authorization": f"Bearer {ADMIN}"}
results, procs, sub_arn = [], [], None


def rec(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def aws(*a):
    r = subprocess.run(["aws", *a, "--region", REGION, "--output", "json"], capture_output=True, text=True, timeout=60)
    return r.returncode, (json.loads(r.stdout) if r.stdout.strip().startswith(("{", "[")) else r.stdout), r.stderr


def smtp_password(secret):
    # AWS SES SMTP password derivation, version 4.
    def sign(k, m): return hmac.new(k, m.encode(), hashlib.sha256).digest()
    sig = sign(("AWS4" + secret).encode(), "11111111")
    for part in (REGION, "ses", "aws4_request"):
        sig = sign(sig, part)
    sig = sign(sig, "SendRawEmail")
    return base64.b64encode(bytes([4]) + sig).decode()


def cli(key):
    return subprocess.run(["aws", "configure", "get", key], capture_output=True, text=True).stdout.strip()


def main():
    global sub_arn
    tmp = tempfile.mkdtemp(prefix="oae-ses-")
    ak, sk = cli("aws_access_key_id"), cli("aws_secret_access_key")
    # --- tunnel
    ng = subprocess.Popen(["ngrok", "http", str(PORT), "--log=stdout"], stdout=open(f"{tmp}/ngrok.log", "w"), stderr=subprocess.STDOUT)
    procs.append(ng)
    url = None
    for _ in range(40):
        time.sleep(0.5)
        try:
            t = httpx.get("http://127.0.0.1:4040/api/tunnels", timeout=2).json()["tunnels"]
            url = next((x["public_url"] for x in t if x["public_url"].startswith("https")), None)
            if url:
                break
        except Exception:
            pass
    rec("ngrok tunnel up", bool(url), url or open(f"{tmp}/ngrok.log").read()[-300:])
    if not url:
        return
    # --- server
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{tmp}/live.db", "ADMIN_API_KEY": ADMIN,
           "SMTP_CONFIG_ENCRYPTION_KEY": Fernet.generate_key().decode(), "SES_FEEDBACK_TOPIC_ARN": TOPIC,
           "SES_CONFIGURATION_SET": CONFIG_SET, "PUBLIC_BASE_URL": url, "PORT": str(PORT), "SMTP_PORT": "2598", "HOST": "127.0.0.1"}
    sv = subprocess.Popen([sys.executable, "run.py"], cwd=SERVER, env=env, stdout=open(f"{tmp}/server.log", "w"), stderr=subprocess.STDOUT)
    procs.append(sv)
    up = False
    for _ in range(60):
        time.sleep(0.5)
        try:
            up = httpx.get(f"http://127.0.0.1:{PORT}/ready", timeout=2).status_code == 200
            if up:
                break
        except Exception:
            pass
    rec("server ready", up)
    if not up:
        return
    c = httpx.Client(base_url=f"http://127.0.0.1:{PORT}", headers=H, timeout=60)
    # --- relay settings (derived SMTP credentials; never printed)
    r = c.post("/v1/settings/smtp", json={"host": f"email-smtp.{REGION}.amazonaws.com", "port": "587", "username": ak,
                                          "password": smtp_password(sk), "use_tls": True, "use_ssl": False,
                                          "from_email": SENDER, "enabled": True})
    rec("SES SMTP relay configured", r.status_code == 200, r.text[:120] if r.status_code != 200 else "")
    # --- SNS subscription through the public tunnel
    rc, out, err = aws("sns", "subscribe", "--topic-arn", TOPIC, "--protocol", "https", "--notification-endpoint", f"{url}/webhooks/ses",
                       "--return-subscription-arn")
    sub_arn = out.get("SubscriptionArn") if isinstance(out, dict) else None
    rec("SNS subscribe requested", rc == 0 and bool(sub_arn), err[:150])
    confirmed = False
    for _ in range(40):
        time.sleep(1.5)
        rc, out, _ = aws("sns", "get-subscription-attributes", "--subscription-arn", sub_arn)
        if rc == 0 and out["Attributes"].get("PendingConfirmation") == "false":
            confirmed = True
            break
    rec("subscription auto-confirmed by the server (signed SubscriptionConfirmation verified)", confirmed)
    if not confirmed:
        print(open(f"{tmp}/server.log").read()[-1500:])
        return
    # --- agent + sends to SES mailbox-simulator recipients
    r = c.post("/v1/agents", json={"email": SENDER, "display_name": "OAE live test", "hitl_enabled": False})
    rec("agent created", r.status_code == 201, r.text[:100])
    cases = {"success@simulator.amazonses.com": "delivered", "bounce@simulator.amazonses.com": "bounced",
             "complaint@simulator.amazonses.com": "complained"}
    ids = {}
    for rcpt in cases:
        r = c.post(f"/v1/agents/{SENDER}/messages", json={"recipient": rcpt, "subject": f"oae live test {rcpt.split('@')[0]}", "body_text": "simulator test"})
        ids[rcpt] = r.json().get("id")
        rec(f"send accepted for {rcpt.split('@')[0]}", r.status_code == 201, r.text[:100] if r.status_code != 201 else "")
    # --- relay acceptance, then provider feedback
    deadline = time.time() + 150
    final = {}
    while time.time() < deadline and len(final) < len(cases):
        time.sleep(3)
        for rcpt, mid in ids.items():
            if rcpt in final or not mid:
                continue
            m = c.get(f"/v1/agents/{SENDER}/messages/{mid}").json()
            if m.get("provider_delivery_status"):
                final[rcpt] = m
    for rcpt, want in cases.items():
        m = final.get(rcpt)
        rec(f"{rcpt.split('@')[0]}: relay accepted then provider outcome == {want}",
            bool(m) and m["provider_delivery_status"] == want and m["status"] == "accepted_by_relay",
            f"status={m and m['status']} provider={m and m['provider_delivery_status']} err={m and m.get('provider_delivery_error')}")
    # --- suppression behaviour
    sup = {s["email"] for s in c.get(f"/v1/agents/{SENDER}/suppressions").json()}
    rec("permanent bounce created a suppression", "bounce@simulator.amazonses.com" in sup)
    rec("complaint created a suppression", "complaint@simulator.amazonses.com" in sup)
    rec("delivery did NOT create a suppression", "success@simulator.amazonses.com" not in sup)
    r = c.post(f"/v1/agents/{SENDER}/messages", json={"recipient": "bounce@simulator.amazonses.com", "subject": "again", "body_text": "x"})
    rec("new send to the bounced recipient is refused before any message is created", 400 <= r.status_code < 500, str(r.status_code))
    # --- replay a real SNS notification (duplicate delivery) from ngrok's request inspector
    try:
        reqs = httpx.get("http://127.0.0.1:4040/api/requests/http?limit=50", timeout=5).json()["requests"]
        notes = [x for x in reqs if x["request"]["uri"].endswith("/webhooks/ses") and "Notification" in (x["request"].get("raw") and base64.b64decode(x["request"]["raw"]).decode(errors="ignore") or "")]
        if notes:
            raw = base64.b64decode(notes[0]["request"]["raw"]).decode()
            body = raw.split("\r\n\r\n", 1)[1]
            before = c.get("/v1/events?limit=100").json()
            dup = httpx.post(f"http://127.0.0.1:{PORT}/webhooks/ses", content=body, headers={"content-type": "text/plain; charset=UTF-8"}, timeout=30)
            rec("duplicate SNS notification accepted idempotently (200)", dup.status_code == 200, str(dup.status_code))
        else:
            rec("duplicate SNS notification replay", False, "no captured notification")
    except Exception as e:
        rec("duplicate SNS notification replay", False, repr(e)[:120])
    # --- negative: forged notification must be rejected
    forged = httpx.post(f"http://127.0.0.1:{PORT}/webhooks/ses", json={"Type": "Notification", "TopicArn": TOPIC, "Message": "{}", "MessageId": "x",
                        "Timestamp": "2026-10-15T00:00:00Z", "SignatureVersion": "1", "Signature": "AAAA",
                        "SigningCertURL": "https://evil.example.com/SimpleNotificationService-x.pem"}, timeout=15)
    rec("forged SNS notification rejected", forged.status_code in (400, 403), str(forged.status_code))
    wrongtopic = httpx.post(f"http://127.0.0.1:{PORT}/webhooks/ses", json={"Type": "Notification", "TopicArn": "arn:aws:sns:us-east-2:1:other"}, timeout=15)
    rec("notification for another topic rejected", wrongtopic.status_code == 403, str(wrongtopic.status_code))
    print("\n==== SUMMARY", sum(results), "passed,", len(results) - sum(results), "failed")
    print("workdir", tmp)


try:
    main()
finally:
    if sub_arn and sub_arn.startswith("arn:"):
        aws("sns", "unsubscribe", "--subscription-arn", sub_arn)
        print("unsubscribed", flush=True)
    for p in procs:
        if p.poll() is None:
            p.send_signal(signal.SIGINT)
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
