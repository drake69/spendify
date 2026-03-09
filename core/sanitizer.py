"""PII sanitization for LLM payloads (RF-10).

Mandatory pre-requisite for any remote LLM call.
Recommended for local LLM as well.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field


# ── Regexes ───────────────────────────────────────────────────────────────────

_IBAN_RE = re.compile(r'\b[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b')
# PAN: 13–19 digit sequences (card numbers)
_PAN_RE = re.compile(r'\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{1,7}\b')
# Masked card (e.g. ****1234 or XXXX-1234)
_MASKED_CARD_RE = re.compile(r'[\*X]{4}[\s\-]?\d{4}')
# Bank transaction codes (e.g. CAU 12345, NDS 99, POS 12345)
_BANK_CODE_RE = re.compile(r'\b(CAU|NDS|POS|TRN|CRO|RIF|ID\s*TRANSAZIONE)\s*[\d\-]+', re.IGNORECASE)
# Italian fiscal code
_CF_RE = re.compile(r'\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b', re.IGNORECASE)


@dataclass
class SanitizationConfig:
    owner_names: list[str] = field(default_factory=list)
    extra_patterns: list[str] = field(default_factory=list)  # additional regex patterns

    def compiled_extras(self) -> list[re.Pattern]:
        return [re.compile(p, re.IGNORECASE) for p in self.extra_patterns]


def redact_pii(text: str, config: SanitizationConfig | None = None) -> str:
    """Replace sensitive tokens with semantic placeholders.

    Replacements:
      IBAN → <ACCOUNT_ID>
      PAN/card number → <CARD_ID>
      Owner names → <OWNER>
      Bank codes → <TX_CODE>
      Fiscal code → <FISCAL_ID>
    """
    if not text:
        return text

    config = config or SanitizationConfig()

    # Owner names (case-insensitive whole-word)
    for name in config.owner_names:
        if name.strip():
            pattern = re.compile(r'\b' + re.escape(name.strip()) + r'\b', re.IGNORECASE)
            text = pattern.sub('<OWNER>', text)

    text = _IBAN_RE.sub('<ACCOUNT_ID>', text)
    text = _PAN_RE.sub('<CARD_ID>', text)
    text = _MASKED_CARD_RE.sub('<CARD_ID>', text)
    text = _BANK_CODE_RE.sub('<TX_CODE>', text)
    text = _CF_RE.sub('<FISCAL_ID>', text)

    for pattern in config.compiled_extras():
        text = pattern.sub('<REDACTED>', text)

    return text


def sanitize_dataframe_descriptions(descriptions: list[str], config: SanitizationConfig | None = None) -> list[str]:
    """Apply PII redaction to a list of description strings."""
    return [redact_pii(d, config) for d in descriptions]


def assert_sanitized(text: str) -> None:
    """Raise ValueError if obvious PII is still present after sanitization."""
    if _IBAN_RE.search(text):
        raise ValueError("Unsanitized IBAN found in payload")
    if _PAN_RE.search(text):
        raise ValueError("Unsanitized card number found in payload")
