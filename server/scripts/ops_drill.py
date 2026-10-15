"""Backup/restore and key-rotation drill against a PostgreSQL server (release-gate evidence).

Boots the real server on PostgreSQL, creates data (message with attachment, webhook secret, domain DKIM key),
dumps and restores it into a fresh database, proves it fails closed with a missing or wrong encryption key,
then rotates the key. Needs pg_dump/pg_restore/psql/createdb on PATH.

    cd server && python scripts/ops_drill.py

Connection defaults match a local throwaway cluster; override with OAE_DRILL_PG_HOST/PORT/USER.
"""
import asyncio, os, signal, subprocess, sys, tempfile, time
import httpx
from cryptography.fernet import Fernet

SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PG_HOST = os.environ.get("OAE_DRILL_PG_HOST", "127.0.0.1")
PG_PORT = os.environ.get("OAE_DRILL_PG_PORT", "55433")
PG_USER = os.environ.get("OAE_DRILL_PG_USER", "oae")
PG = ["-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER]
PORT = 8811
ADMIN = "drill-admin-key"
H = {"Authorization": f"Bearer {ADMIN}"}
K1, K2 = Fernet.generate_key().decode(), Fernet.generate_key().decode()
results = []


def rec(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def start(db, key, prev=""):
    env = {**os.environ, "DATABASE_URL": f"postgresql+asyncpg://{PG_USER}@{PG_HOST}:{PG_PORT}/{db}",
           "ADMIN_API_KEY": ADMIN, "SMTP_CONFIG_ENCRYPTION_KEY": key, "SMTP_CONFIG_ENCRYPTION_KEY_PREVIOUS": prev,
           "PORT": str(PORT), "SMTP_PORT": "2599", "HOST": "127.0.0.1"}
    p = subprocess.Popen([sys.executable, "run.py"], cwd=SERVER, env=env, stdout=open(f"/tmp/oae-drill-{db}.log", "w"),
                         stderr=subprocess.STDOUT)
    for _ in range(60):
        time.sleep(0.5)
        if p.poll() is not None:
            return p, False
        try:
            if httpx.get(f"http://127.0.0.1:{PORT}/ready", timeout=2).status_code == 200:
                return p, True
        except Exception:
            pass
    return p, False


def stop(p):
    if p.poll() is None:
        p.send_signal(signal.SIGINT)
        try:
            p.wait(15)
        except subprocess.TimeoutExpired:
            p.kill()


def db_secret(db, sql):
    return sh("psql", *PG, "-d", db, "-tAc", sql).stdout.strip()


def decrypt(secret_blob, key, prev=""):
    os.environ["SMTP_CONFIG_ENCRYPTION_KEY"], os.environ["SMTP_CONFIG_ENCRYPTION_KEY_PREVIOUS"] = key, prev
    sys.path.insert(0, SERVER)
    from app.config import settings
    settings.SMTP_CONFIG_ENCRYPTION_KEY, settings.SMTP_CONFIG_ENCRYPTION_KEY_PREVIOUS = key, prev
    from app.services.smtp_secrets import decrypt_secret
    return decrypt_secret(secret_blob)


async def main():
    for db in ("oae_src", "oae_dst"):
        sh("dropdb", *PG, "--if-exists", db)
    sh("createdb", *PG, "oae_src")

    # ---- 1. populate a real instance on PostgreSQL
    p, up = start("oae_src", K1)
    rec("server boots on PostgreSQL with key K1", up)
    if not up:
        print(open("/tmp/oae-drill-oae_src.log").read()[-1500:]); return
    c = httpx.Client(base_url=f"http://127.0.0.1:{PORT}", headers=H, timeout=30)
    c.post("/v1/agents", json={"email": "drill@agents.local", "display_name": "Drill", "hitl_enabled": False}).raise_for_status()
    m = c.post("/v1/agents/drill@agents.local/messages", json={"recipient": "to@example.net", "subject": "backup me",
                                                              "body_text": "hello", "attachments": [
        {"filename": "a.txt", "content_type": "text/plain", "content_base64": "aGVsbG8gYXR0YWNobWVudA=="}]})
    rec("message with attachment stored", m.status_code == 201, m.text[:80] if m.status_code != 201 else "")
    mid = m.json()["id"]
    wh = c.post("/v1/webhooks", json={"agent_email": "drill@agents.local", "url": "https://example.com/hook", "events": ["*"]})
    rec("webhook created (secret returned once)", wh.status_code == 201, wh.text[:100] if wh.status_code != 201 else "")
    wsecret = wh.json().get("secret") or wh.json().get("signing_secret")
    d = c.post("/v1/domains", json={"domain": "drill.example.com"})
    rec("domain registered with generated DKIM key", d.status_code == 201)
    rec("at-rest secrets are encrypted",
        db_secret("oae_src", "select signing_secret from webhook_endpoints").startswith("fernet:v1:")
        and db_secret("oae_src", "select dkim_private_key from domains").startswith("fernet:v1:"))
    stop(p)

    # ---- 2. dump + restore into a fresh database
    t = time.time()
    dump = sh("pg_dump", *PG, "-Fc", "-f", "/tmp/oae-drill.dump", "oae_src")
    rec("pg_dump", dump.returncode == 0, dump.stderr[:100])
    sh("createdb", *PG, "oae_dst")
    rs = sh("pg_restore", *PG, "--no-owner", "-d", "oae_dst", "/tmp/oae-drill.dump")
    rec("pg_restore into fresh database", rs.returncode == 0, rs.stderr[:200])
    rec("restore took", True, f"{time.time() - t:.1f}s")

    # ---- 3. restore WITHOUT the key must fail closed; wrong key must fail closed
    bad, up_bad = start("oae_dst", Fernet.generate_key().decode())
    rec("restored DB + WRONG key refuses to start", not up_bad)
    stop(bad)
    nokey, up_nokey = start("oae_dst", "")
    rec("restored DB + NO key refuses to start", not up_nokey)
    stop(nokey)

    # ---- 4. restore with the matching key works and data is intact
    p, up = start("oae_dst", K1)
    rec("restored DB + original key boots and is ready", up)
    c = httpx.Client(base_url=f"http://127.0.0.1:{PORT}", headers=H, timeout=30)
    got = c.get(f"/v1/agents/drill@agents.local/messages/{mid}")
    rec("message readable after restore", got.status_code == 200 and got.json()["subject"] == "backup me")
    atts = c.get(f"/v1/agents/drill@agents.local/messages/{mid}/attachments").json()
    rec("attachment readable after restore", len(atts) == 1)
    if atts:
        body = c.get(f"/v1/agents/drill@agents.local/messages/{mid}/attachments/{atts[0]['id']}").content
        rec("attachment bytes identical", body == b"hello attachment")
    rec("webhook secret decrypts to the original", decrypt(db_secret("oae_dst", "select signing_secret from webhook_endpoints"), K1) == wsecret)
    stop(p)

    # ---- 5. key rotation K1 -> K2
    p, up = start("oae_dst", K2, K1)
    rec("rotation: start with new key + previous key", up)
    stop(p)
    blob = db_secret("oae_dst", "select signing_secret from webhook_endpoints")
    rec("rotation: secret re-encrypted under K2 (K1 can no longer read it)", True) if _only_k2(blob, K1, K2, wsecret) else rec("rotation: secret re-encrypted under K2", False)
    p, up = start("oae_dst", K2)
    rec("rotation: previous key removed, still boots", up)
    stop(p)
    old, up_old = start("oae_dst", K1)
    rec("rotation: old key alone no longer works", not up_old)
    stop(old)

    print("\n==== SUMMARY", sum(1 for _, ok, _ in results if ok), "passed,", sum(1 for _, ok, _ in results if not ok), "failed")


def _only_k2(blob, k1, k2, secret):
    try:
        if decrypt(blob, k2) != secret:
            return False
    except Exception:
        return False
    try:
        decrypt(blob, k1)
        return False
    except Exception:
        return True


asyncio.run(main())
