"""Two-worker concurrency drill against PostgreSQL (release-gate evidence).

Seeds N queued sends into one database, then runs several separate worker processes (the same
`run_outbound_worker` loop the server runs) against it while a counting local SMTP relay records every
submission. Proves each job is claimed once, each recipient receives exactly one copy, and that more than
one process actually took part.

    cd server && python scripts/two_worker_drill.py [--jobs 200] [--workers 2]

Defaults match a throwaway local cluster; override with OAE_DRILL_PG_HOST/PORT/USER and OAE_DRILL_DB.
"""
import argparse
import asyncio
import collections
import datetime
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time

SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVER)

PG_HOST = os.environ.get("OAE_DRILL_PG_HOST", "127.0.0.1")
PG_PORT = os.environ.get("OAE_DRILL_PG_PORT", "5433")
PG_USER = os.environ.get("OAE_DRILL_PG_USER", "oae")
PG_DB = os.environ.get("OAE_DRILL_DB", "oae_drill")
RELAY_PORT = int(os.environ.get("OAE_DRILL_RELAY_PORT", "2598"))
URL = f"postgresql+asyncpg://{PG_USER}@{PG_HOST}:{PG_PORT}/{PG_DB}"

os.environ.setdefault("DATABASE_URL", URL)
os.environ.setdefault("ADMIN_API_KEY", "drill-admin-key")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("SMTP_CONFIG_ENCRYPTION_KEY", Fernet.generate_key().decode())

WORKER_CODE = """
import asyncio, logging, sys
logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("outbound_worker").setLevel(logging.DEBUG)
from app.services.outbound_worker import run_outbound_worker
asyncio.run(run_outbound_worker())
"""


class CountingRelay:
    """Accepts every message, optionally slowly, and counts deliveries per recipient."""

    def __init__(self, delay: float):
        self.delay = delay
        self.counts: collections.Counter = collections.Counter()
        self.lock = threading.Lock()

    async def handle_DATA(self, server, session, envelope):
        if self.delay:
            await asyncio.sleep(self.delay)
        with self.lock:
            for rcpt in envelope.rcpt_tos:
                self.counts[rcpt.lower()] += 1
        return "250 OK"


def rec(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)
    return ok


async def seed(n: int, run_id: str, available_at: datetime.datetime):
    from app.database import AsyncSessionLocal, Base, engine
    from app.models import Agent, Message, OutboundJob, SmtpConfig

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    now = datetime.datetime.utcnow()
    sender = f"drill-{run_id}@agents.example.com"
    async with AsyncSessionLocal() as db:
        db.add(Agent(id=f"ag_{run_id}", email=sender, display_name="Drill", hitl_enabled=False, created_at=now))
        db.add(SmtpConfig(id="default", host="127.0.0.1", port=str(RELAY_PORT), username="", password="",
                          use_tls=False, use_ssl=False, from_email=sender, enabled=True))
        await db.flush()
        for i in range(n):
            mid = f"msg_{run_id}_{i:04d}"
            db.add(Message(
                id=mid, agent_email=sender, direction="outbound", is_read=True, sender=sender,
                recipient=f"rcpt{i:04d}@example.org", subject=f"drill {i}", body_text="drill body",
                thread_id=f"th_{run_id}_{i:04d}", rfc_message_id=f"<{mid}@drill.invalid>", status="sending",
                created_at=now))
            db.add(OutboundJob(id=f"job_{run_id}_{i:04d}", message_id=mid, status="pending",
                               available_at=available_at, created_at=now))
        await db.commit()


async def wait_done(n: int, timeout: float):
    from sqlalchemy import func, select
    from app.database import AsyncSessionLocal
    from app.models import OutboundJob

    deadline = time.time() + timeout
    while time.time() < deadline:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(OutboundJob.status, func.count()).group_by(OutboundJob.status))).all()
        by = dict(rows)
        if by.get("complete", 0) + by.get("failed", 0) + by.get("uncertain", 0) + by.get("blocked", 0) == n:
            return by
        await asyncio.sleep(0.5)
    return by


async def collect(n: int):
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models import Message, OutboundJob

    async with AsyncSessionLocal() as db:
        jobs = (await db.execute(select(OutboundJob))).scalars().all()
        messages = (await db.execute(select(Message))).scalars().all()
    return jobs, messages


async def amain() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=200)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--relay-delay", type=float, default=0.02, help="seconds the relay holds each message")
    args = ap.parse_args()

    from aiosmtpd.controller import Controller

    relay = CountingRelay(args.relay_delay)
    controller = Controller(relay, hostname="127.0.0.1", port=RELAY_PORT)
    controller.start()
    run_id = str(int(time.time()))
    # Jobs become claimable at the same instant so every worker races for the whole batch.
    start_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=6)
    await seed(args.jobs, run_id, start_at)
    print(f"seeded {args.jobs} jobs; {args.workers} workers start claiming at {start_at.isoformat()}Z", flush=True)

    logs = [tempfile.NamedTemporaryFile("w+", prefix=f"oae-worker{i}-", delete=False) for i in range(args.workers)]
    procs = [subprocess.Popen([sys.executable, "-c", WORKER_CODE], cwd=SERVER, env=os.environ.copy(),
                              stdout=logs[i], stderr=subprocess.STDOUT) for i in range(args.workers)]
    ok = True
    try:
        by_status = await wait_done(args.jobs, 120)
        await asyncio.sleep(2)  # let any duplicate in flight reach the relay before counting
    finally:
        for p in procs:
            p.send_signal(signal.SIGINT)
        for p in procs:
            try:
                p.wait(15)
            except subprocess.TimeoutExpired:
                p.kill()
        controller.stop()

    jobs, messages = await collect(args.jobs)
    claims = collections.Counter()
    per_worker = []
    for log in logs:
        log.flush()
        text = open(log.name).read()
        ids = re.findall(r"claimed outbound job (\S+) attempt", text)
        per_worker.append(len(ids))
        claims.update(ids)
        os.unlink(log.name)

    ok &= rec("all jobs reached a terminal state", sum(by_status.values()) == args.jobs, str(by_status))
    ok &= rec("every job completed", by_status.get("complete", 0) == args.jobs)
    ok &= rec("every job used exactly one attempt", all(j.attempts == 1 for j in jobs),
              f"max attempts {max(j.attempts for j in jobs)}")
    ok &= rec("no job was claimed more than once", all(c == 1 for c in claims.values()) and len(claims) == args.jobs,
              f"{len(claims)} distinct claims, max {max(claims.values()) if claims else 0}")
    dup = {r: c for r, c in relay.counts.items() if c != 1}
    ok &= rec("relay received exactly one copy per recipient",
              len(relay.counts) == args.jobs and not dup, f"{len(relay.counts)} recipients, duplicates {len(dup)}")
    ok &= rec("every message ended accepted_by_relay", all(m.status == "accepted_by_relay" for m in messages),
              str(collections.Counter(m.status for m in messages)))
    ok &= rec("more than one worker took part", sum(1 for c in per_worker if c) >= min(2, args.workers),
              f"claims per worker {per_worker}")
    print("RESULT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    return asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())
