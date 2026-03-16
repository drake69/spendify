"""PII sanitization for LLM payloads (RF-10).

Mandatory pre-requisite for any remote LLM call.
Recommended for local LLM as well.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from itertools import permutations as _iter_permutations


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


# ── Fake name pools (per language) ────────────────────────────────────────────
#
# Owner names are replaced with plausible fake names before LLM calls so the
# model can recognise and extract them as person names.  After the LLM returns
# its result, restore_owner_aliases() substitutes the fake names back with the
# real owner names.
#
# Names were chosen to be realistic but uncommon enough that they are very
# unlikely to collide with actual counterpart names in bank descriptions.

_FAKE_NAME_POOLS: dict[str, list[str]] = {
    "it": [
        "Carlo Brambilla", "Marta Pellegrino", "Alberto Marini",
        "Giovanna Ferrara", "Luca Montanari", "Silvia Cattaneo",
    ],
    "fr": [
        "Pierre Dumont", "Claire Lebrun", "Michel Garnier",
        "Sophie Renard", "Philippe Blanc", "Isabelle Morin",
    ],
    "de": [
        "Klaus Hartmann", "Monika Braun", "Stefan Richter",
        "Ingrid Weber", "Dieter Schulz", "Brigitte Krause",
    ],
    "en": [
        "James Fletcher", "Helen Norris", "David Lawson",
        "Susan Palmer", "Robert Carey", "Patricia Holt",
    ],
    "es": [
        "Carlos Navarro", "Elena Vega", "Miguel Torres",
        "Isabel Molina", "Fernando Castro", "Carmen Ruiz",
    ],
}


def _get_fake_name(index: int, language: str = "it") -> str:
    pool = _FAKE_NAME_POOLS.get(language) or _FAKE_NAME_POOLS["it"]
    return pool[index % len(pool)]


@dataclass
class SanitizationConfig:
    owner_names: list[str] = field(default_factory=list)
    extra_patterns: list[str] = field(default_factory=list)  # additional regex patterns
    description_language: str = "it"  # used to select the fake name pool

    def compiled_extras(self) -> list[re.Pattern]:
        return [re.compile(p, re.IGNORECASE) for p in self.extra_patterns]


def redact_pii(text: str, config: SanitizationConfig | None = None) -> str:
    """Replace sensitive tokens with safe substitutes.

    Owner name replacements:
      Each owner name is swapped with a plausible fake name from the pool for
      the configured language.  The LLM sees a realistic person name and can
      extract it correctly.  Call restore_owner_aliases() on the result to put
      the real names back.

    Other replacements:
      IBAN           → <ACCOUNT_ID>
      PAN/card number→ <CARD_ID>
      Bank codes     → <TX_CODE>
      Fiscal code    → <FISCAL_ID>
    """
    if not text:
        return text

    config = config or SanitizationConfig()

    # Owner names — replaced with fake but plausible names.
    # All token-permutations are matched so that both "Luigi Corsaro" and
    # "Corsaro Luigi" (surname-first, common in Italian bank exports) are caught.
    for i, name in enumerate(config.owner_names):
        name = name.strip()
        if not name:
            continue
        fake = _get_fake_name(i, config.description_language)
        tokens = name.split()
        if len(tokens) == 1:
            pattern = re.compile(r'\b' + re.escape(tokens[0]) + r'\b', re.IGNORECASE)
            text = pattern.sub(fake, text)
        else:
            perm_patterns = [
                r'\b' + r'\s+'.join(re.escape(t) for t in perm) + r'\b'
                for perm in _iter_permutations(tokens)
            ]
            combined = re.compile('|'.join(f'(?:{p})' for p in perm_patterns), re.IGNORECASE)
            text = combined.sub(fake, text)

    text = _IBAN_RE.sub('<ACCOUNT_ID>', text)
    text = _PAN_RE.sub('<CARD_ID>', text)
    text = _MASKED_CARD_RE.sub('<CARD_ID>', text)
    text = _BANK_CODE_RE.sub('<TX_CODE>', text)
    text = _CF_RE.sub('<FISCAL_ID>', text)

    for pattern in config.compiled_extras():
        text = pattern.sub('<REDACTED>', text)

    return text


def restore_owner_aliases(text: str, config: SanitizationConfig | None = None) -> str:
    """Replace fake owner names with the corresponding real owner names.

    Call this on any text returned by the LLM that was previously processed by
    redact_pii(), so that the real names are reinstated for downstream logic
    (e.g. giroconto detection that matches on owner names).
    """
    if not text or not config or not config.owner_names:
        return text

    for i, name in enumerate(config.owner_names):
        name = name.strip()
        if name:
            fake = _get_fake_name(i, config.description_language)
            pattern = re.compile(r'\b' + re.escape(fake) + r'\b', re.IGNORECASE)
            text = pattern.sub(name, text)

    return text


# Keep old name as alias for callers that haven't been updated yet.
restore_owner_placeholders = restore_owner_aliases


def sanitize_dataframe_descriptions(descriptions: list[str], config: SanitizationConfig | None = None) -> list[str]:
    """Apply PII redaction to a list of description strings."""
    return [redact_pii(d, config) for d in descriptions]


def assert_sanitized(text: str) -> None:
    """Raise ValueError if obvious PII is still present after sanitization."""
    if _IBAN_RE.search(text):
        raise ValueError("Unsanitized IBAN found in payload")
    if _PAN_RE.search(text):
        raise ValueError("Unsanitized card number found in payload")
