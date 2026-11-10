"""PII masking for logs, traces and the audit log (R9/E4): emails, phone numbers, payment details."""
from __future__ import annotations

import re
from typing import Any

_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[CARD]"),                      # card-like digit runs
    (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"), "[IBAN]"),
    (re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)"), "[PHONE]"),
    (re.compile(r"(?i)\b(paypal|venmo|cashapp|western union|wise)\s*[:@]?\s*\S+"), r"\1 [PAYMENT]"),
]


def mask_text(text: str) -> str:
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


def mask(obj: Any) -> Any:
    """Recursively mask strings inside dicts/lists. Keys named like secrets are dropped entirely."""
    if isinstance(obj, str):
        return mask_text(obj)
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if _secret_key(k) else mask(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [mask(v) for v in obj]
    return obj


def _secret_key(k: Any) -> bool:
    k = str(k).lower()
    return any(s in k for s in ("token", "secret", "password", "authorization", "api_key"))
