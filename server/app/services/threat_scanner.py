import re
import base64
from typing import List, Tuple
from app.schemas import ThreatDetail, ThreatScanResult

class ThreatScanner:
    """
    Advanced Heuristic Threat and Prompt Injection Scanner.
    Inspects inbound emails for indirect prompt injections, hidden content smuggling,
    jailbreak attempts, and data exfiltration vectors before LLM agents process them.
    """

    PROMPT_OVERRIDE_PATTERNS = [
        (r"(?i)ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|directions|prompts|guidelines)", "Direct Instruction Override", 0.95),
        (r"(?i)disregard\s+(all\s+)?(previous|prior)\s+(rules|prompts|context)", "Context Disregard Attempt", 0.95),
        (r"(?i)you\s+are\s+now\s+(a\s+new|an\s+unrestricted|unfiltered|DAN|developer\s+mode)", "Persona Hijacking / Jailbreak", 0.90),
        (r"(?i)(new|updated)\s+system\s+(prompt|instructions|directive)\s*:", "System Prompt Injection Tag", 0.85),
        (r"(?i)from\s+now\s+on\s+you\s+(will|must|should)\s+act\s+as", "Role Override Command", 0.80),
        (r"(?i)bypass\s+(safety|content|security)\s+filters", "Filter Bypass Instruction", 0.90),
        (r"(?i)<\s*\|\s*(im_start|im_end|system|user|assistant)\s*\|\s*>", "ChatML Delimiter Smuggling", 0.95),
        (r"(?i)\[\s*INST\s*\].*?\[\s*/INST\s*\]", "Llama/Mistral Token Smuggling", 0.90),
        (r"(?i)###\s*(System|Instruction|Response)\s*:", "Instruction Delimiter Mimicry", 0.75),
    ]

    EXFILTRATION_PATTERNS = [
        (r"(?i)!\[.*?\]\(https?:\/\/[^\s\)]+[\?&](key|token|auth|secret|leak|data|msg)=", "Markdown Image Exfiltration URL", 0.90),
        (r"(?i)send\s+(this|the|all)\s+(system\s+prompt|api\s+key|passwords?|credentials?)\s+to", "Credential Exfiltration Request", 0.85),
        (r"(?i)repeat\s+(everything|the\s+entire\s+prompt|all\s+text\s+above)", "Prompt Leakage Attempt", 0.70),
    ]

    HIDDEN_HTML_PATTERNS = [
        (r"(?i)display\s*:\s*none", "Hidden CSS Attribute (display:none)", 0.85),
        (r"(?i)visibility\s*:\s*hidden", "Hidden CSS Attribute (visibility:hidden)", 0.85),
        (r"(?i)font-size\s*:\s*0(px|pt|em)?", "Zero-Size Text Smuggling", 0.90),
        (r"(?i)opacity\s*:\s*0(\.0*)?", "Zero-Opacity Hidden Text", 0.85),
        (r"(?i)color\s*:\s*(transparent|#fff(fff)?|white)", "Low-Contrast/Invisible Font Concealment", 0.75),
        (r"[\u200B-\u200D\uFEFF]", "Zero-Width Invisible Unicode Characters", 0.80),
    ]

    SUSPICIOUS_ENCODINGS = [
        (r"[A-Za-z0-9+/]{40,}={0,2}", "Long Suspicious Base64 Blob", 0.50),
    ]

    @classmethod
    def scan(cls, subject: str, body_text: str, body_html: str = None, sensitivity: str = "medium") -> ThreatScanResult:
        """
        Scans subject, text, and html payloads. Returns ThreatScanResult with verdict:
        - 'allow': safe to deliver directly to agent
        - 'review': hold for Human-in-the-Loop review
        - 'block': drop or quarantine immediately
        """
        content = f"{subject or ''}\n\n{body_text or ''}"
        html_content = body_html or ""
        detected: List[ThreatDetail] = []
        max_score = 0.0

        # 1. Check prompt overrides
        for pattern, label, weight in cls.PROMPT_OVERRIDE_PATTERNS:
            match = re.search(pattern, content)
            if match:
                detected.append(ThreatDetail(
                    threat_type="prompt_injection",
                    severity="high" if weight >= 0.85 else "medium",
                    confidence=weight,
                    description=f"Detected pattern: {label}",
                    matched_pattern=match.group(0)[:60]
                ))
                max_score = max(max_score, weight)

        # 2. Check exfiltration
        for pattern, label, weight in cls.EXFILTRATION_PATTERNS:
            match = re.search(pattern, content)
            if match:
                detected.append(ThreatDetail(
                    threat_type="data_exfiltration",
                    severity="high",
                    confidence=weight,
                    description=f"Detected exfiltration vector: {label}",
                    matched_pattern=match.group(0)[:60]
                ))
                max_score = max(max_score, weight)

        # 3. Check hidden HTML and zero-width smuggling
        combined_text = content + "\n" + html_content
        for pattern, label, weight in cls.HIDDEN_HTML_PATTERNS:
            match = re.search(pattern, combined_text)
            if match:
                detected.append(ThreatDetail(
                    threat_type="hidden_payload",
                    severity="medium" if weight < 0.85 else "high",
                    confidence=weight,
                    description=f"Concealed text evasion: {label}",
                    matched_pattern=match.group(0)[:40]
                ))
                max_score = max(max_score, weight)

        # 4. Check base64 encoded chunks in text
        for pattern, label, weight in cls.SUSPICIOUS_ENCODINGS:
            matches = re.finditer(pattern, content)
            for m in matches:
                candidate = m.group(0)
                try:
                    decoded = base64.b64decode(candidate).decode('utf-8', errors='ignore')
                    # If decoded content contains instruction keywords, escalate score
                    if any(kw in decoded.lower() for kw in ["system", "ignore", "prompt", "bypass", "exec", "eval"]):
                        detected.append(ThreatDetail(
                            threat_type="encoded_payload",
                            severity="critical",
                            confidence=0.95,
                            description="Base64 obfuscated prompt injection command",
                            matched_pattern=candidate[:40] + "..."
                        ))
                        max_score = max(max_score, 0.95)
                        break
                except Exception:
                    pass

        # Calculate final threat score (composite)
        if detected:
            threat_score = min(1.0, round(max_score + (len(detected) - 1) * 0.05, 2))
        else:
            threat_score = 0.0

        # Sensitivity thresholds mapping
        # sensitivity: off -> always allow
        # sensitivity: low -> review >= 0.70, block >= 0.90
        # sensitivity: medium -> review >= 0.50, block >= 0.85
        # sensitivity: high -> review >= 0.30, block >= 0.70
        if sensitivity == "off":
            verdict = "allow"
        elif sensitivity == "low":
            if threat_score >= 0.90:
                verdict = "block"
            elif threat_score >= 0.70:
                verdict = "review"
            else:
                verdict = "allow"
        elif sensitivity == "high":
            if threat_score >= 0.70:
                verdict = "block"
            elif threat_score >= 0.30:
                verdict = "review"
            else:
                verdict = "allow"
        else:  # medium (default)
            if threat_score >= 0.85:
                verdict = "block"
            elif threat_score >= 0.50:
                verdict = "review"
            else:
                verdict = "allow"

        summary = (
            f"Clean email (score {threat_score})" if not detected
            else f"Identified {len(detected)} security signals. Overall risk score: {threat_score}. Action: {verdict.upper()}."
        )

        return ThreatScanResult(
            threat_score=threat_score,
            verdict=verdict,
            detected_threats=detected,
            analysis_summary=summary
        )
