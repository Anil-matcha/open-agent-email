"""Optional second inbound screen: agent-mail-gate.

Disabled unless ``AGENT_MAIL_GATE_CONFIG`` names an agent-mail-gate TOML file. The screen reads the
raw RFC 5322 message, so it sees what the body scanner does not: look-alike and unverified sender
domains, a Reply-To that differs from From, risky links and dangerous attachment names. It is
deterministic (no model call) and can only make a verdict stricter: ``quarantine`` holds the message
for review, ``trash`` blocks it, ``clean`` changes nothing.
"""

import asyncio
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from app.config import settings
from app.schemas import ThreatDetail, ThreatScanResult

_RANK = {"allow": 0, "review": 1, "block": 2}
_VERDICTS = {"clean": "allow", "quarantine": "review", "trash": "block"}


@lru_cache(maxsize=4)
def _load(path: str) -> Tuple[Callable[..., Any], Any]:
    try:
        from agentmailgate import config, screen
    except ImportError as exc:
        raise RuntimeError("AGENT_MAIL_GATE_CONFIG is set, but the agent-mail-gate package is not installed") from exc
    return screen.screen, config.load(Path(path)).screening


def check_configured() -> None:
    """Fail at startup, not on the first message, when the screen is set but cannot load."""
    if settings.AGENT_MAIL_GATE_CONFIG:
        _load(settings.AGENT_MAIL_GATE_CONFIG)


async def apply(raw_message: Optional[bytes], sensitivity: str, result: ThreatScanResult) -> ThreatScanResult:
    """Return ``result``, made stricter if agent-mail-gate holds or trashes the raw message."""
    if not settings.AGENT_MAIL_GATE_CONFIG or raw_message is None or sensitivity == "off":
        return result
    screen, screening = _load(settings.AGENT_MAIL_GATE_CONFIG)
    gate = await asyncio.to_thread(screen, raw_message, screening)
    # An unknown disposition fails closed to review.
    verdict = _VERDICTS.get(gate.disposition, "review")
    if verdict == "allow":
        return result
    details = [
        ThreatDetail(
            threat_type="agent_mail_gate",
            severity="high" if verdict == "block" else "medium",
            confidence=1.0,
            description=f"agent-mail-gate {gate.disposition}: {reason}",
        )
        for reason in (gate.reasons or ("no reason given",))
    ]
    final = verdict if _RANK[verdict] > _RANK[result.verdict] else result.verdict
    return ThreatScanResult(
        threat_score=result.threat_score,
        verdict=final,
        detected_threats=[*result.detected_threats, *details],
        analysis_summary=f"{result.analysis_summary} agent-mail-gate: {gate.disposition} "
                         f"({', '.join(gate.reasons) or 'no reason given'}). Action: {final.upper()}.",
    )
