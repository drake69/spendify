"""Tests for PII redaction (RF-10) in core/sanitizer.py."""
from __future__ import annotations

import pytest

from core.sanitizer import (
    SanitizationConfig,
    assert_sanitized,
    redact_pii,
    restore_owner_aliases,
    sanitize_dataframe_descriptions,
)


# ─────────────────────────────────────────────────────────────────────────────
# redact_pii — IBAN
# ─────────────────────────────────────────────────────────────────────────────

class TestRedactIban:
    def test_iban_replaced_with_account_id(self):
        text = "Bonifico a IT60X0542811101000000123456"
        result = redact_pii(text)
        assert "<ACCOUNT_ID>" in result
        assert "IT60X0542811101000000123456" not in result

    def test_iban_at_start_of_string(self):
        text = "IT60X0542811101000000123456 beneficiario"
        result = redact_pii(text)
        assert "<ACCOUNT_ID>" in result

    def test_iban_multiple_occurrences(self):
        text = "da IT60X0542811101000000123456 a IT60X0542811101000000654321"
        result = redact_pii(text)
        assert result.count("<ACCOUNT_ID>") == 2
        assert "IT60" not in result

    def test_no_iban_unchanged(self):
        text = "pagamento supermercato"
        assert redact_pii(text) == text


# ─────────────────────────────────────────────────────────────────────────────
# redact_pii — card number (PAN)
# ─────────────────────────────────────────────────────────────────────────────

class TestRedactPan:
    def test_16_digit_card_number_replaced(self):
        text = "addebito carta 4111 1111 1111 1111"
        result = redact_pii(text)
        assert "<CARD_ID>" in result
        assert "4111" not in result

    def test_masked_card_number_replaced(self):
        text = "carta ****1234 addebito"
        result = redact_pii(text)
        assert "<CARD_ID>" in result
        assert "****1234" not in result

    def test_masked_card_xxxx_replaced(self):
        text = "pagamento XXXX-5678"
        result = redact_pii(text)
        assert "<CARD_ID>" in result

    def test_13_digit_card_number_replaced(self):
        text = "carta 4111111111116"
        result = redact_pii(text)
        assert "<CARD_ID>" in result


# ─────────────────────────────────────────────────────────────────────────────
# redact_pii — fiscal code (CF)
# ─────────────────────────────────────────────────────────────────────────────

class TestRedactFiscalCode:
    def test_fiscal_code_replaced_with_fiscal_id(self):
        text = "intestatario RSSMRA80A01H501Z pagamento"
        result = redact_pii(text)
        assert "<FISCAL_ID>" in result
        assert "RSSMRA80A01H501Z" not in result

    def test_fiscal_code_case_insensitive(self):
        text = "intestatario rssmra80a01h501z"
        result = redact_pii(text)
        assert "<FISCAL_ID>" in result

    def test_partial_match_not_redacted(self):
        """Short alphanumeric sequences that don't match the full CF pattern are kept."""
        text = "operazione ABC123"
        result = redact_pii(text)
        assert "ABC123" in result


# ─────────────────────────────────────────────────────────────────────────────
# redact_pii — owner name substitution
# ─────────────────────────────────────────────────────────────────────────────

class TestRedactOwnerName:
    def test_owner_name_replaced_with_fake(self):
        cfg = SanitizationConfig(owner_names=["Mario Rossi"], description_language="it")
        text = "bonifico Mario Rossi conto deposito"
        result = redact_pii(text, cfg)
        assert "Mario Rossi" not in result
        # Replaced with a fake Italian name from the pool
        assert any(fake in result for fake in [
            "Carlo Brambilla", "Marta Pellegrino", "Alberto Marini",
            "Giovanna Ferrara", "Luca Montanari", "Silvia Cattaneo",
        ])

    def test_owner_name_surname_first_replaced(self):
        cfg = SanitizationConfig(owner_names=["Mario Rossi"], description_language="it")
        text = "bonifico ROSSI MARIO"
        result = redact_pii(text, cfg)
        assert "ROSSI" not in result or "MARIO" not in result  # at least one token replaced

    def test_single_token_owner_replaced(self):
        cfg = SanitizationConfig(owner_names=["Corsaro"])
        text = "da Corsaro mensile"
        result = redact_pii(text, cfg)
        assert "Corsaro" not in result

    def test_no_owner_names_no_substitution(self):
        cfg = SanitizationConfig(owner_names=[])
        text = "mario rossi bonifico"
        result = redact_pii(text, cfg)
        assert result == text

    def test_empty_owner_name_skipped(self):
        cfg = SanitizationConfig(owner_names=["", "Mario Rossi"])
        text = "bonifico Mario Rossi"
        result = redact_pii(text, cfg)
        assert "Mario Rossi" not in result

    def test_multiple_owners_replaced(self):
        cfg = SanitizationConfig(owner_names=["Mario Rossi", "Anna Bianchi"])
        text = "da Mario Rossi per conto di Anna Bianchi"
        result = redact_pii(text, cfg)
        assert "Mario Rossi" not in result
        assert "Anna Bianchi" not in result

    def test_french_language_uses_french_pool(self):
        cfg = SanitizationConfig(owner_names=["Jean Dupont"], description_language="fr")
        text = "virement Jean Dupont"
        result = redact_pii(text, cfg)
        assert "Jean Dupont" not in result
        assert any(fake in result for fake in [
            "Pierre Dumont", "Claire Lebrun", "Michel Garnier",
            "Sophie Renard", "Philippe Blanc", "Isabelle Morin",
        ])


# ─────────────────────────────────────────────────────────────────────────────
# redact_pii — extra patterns
# ─────────────────────────────────────────────────────────────────────────────

class TestRedactExtraPatterns:
    def test_extra_pattern_replaced_with_redacted(self):
        cfg = SanitizationConfig(extra_patterns=[r"\d{6,}"])
        text = "operazione 1234567890"
        result = redact_pii(text, cfg)
        assert "1234567890" not in result
        assert "<REDACTED>" in result

    def test_multiple_extra_patterns(self):
        cfg = SanitizationConfig(extra_patterns=[r"SECRET", r"PRIVATE"])
        text = "pagamento SECRET di PRIVATE"
        result = redact_pii(text, cfg)
        assert "SECRET" not in result
        assert "PRIVATE" not in result

    def test_empty_extra_patterns_no_change(self):
        cfg = SanitizationConfig(extra_patterns=[])
        text = "normale descrizione"
        assert redact_pii(text, cfg) == text


# ─────────────────────────────────────────────────────────────────────────────
# redact_pii — edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestRedactPiiEdgeCases:
    def test_empty_string_returned_unchanged(self):
        assert redact_pii("") == ""

    def test_none_config_uses_defaults(self):
        text = "addebito IT60X0542811101000000123456"
        result = redact_pii(text, None)
        assert "<ACCOUNT_ID>" in result

    def test_non_sensitive_text_unchanged(self):
        text = "pagamento supermercato esselunga"
        assert redact_pii(text) == text


# ─────────────────────────────────────────────────────────────────────────────
# restore_owner_aliases
# ─────────────────────────────────────────────────────────────────────────────

class TestRestoreOwnerAliases:
    def test_round_trip(self):
        cfg = SanitizationConfig(owner_names=["Luigi Corsaro"], description_language="it")
        original = "bonifico Luigi Corsaro conto deposito"
        sanitized = redact_pii(original, cfg)
        restored = restore_owner_aliases(sanitized, cfg)
        assert "Luigi Corsaro" in restored

    def test_restore_no_config_returns_unchanged(self):
        text = "qualche testo"
        assert restore_owner_aliases(text, None) == text

    def test_restore_empty_owners_returns_unchanged(self):
        cfg = SanitizationConfig(owner_names=[])
        text = "Carlo Brambilla testo"
        assert restore_owner_aliases(text, cfg) == text

    def test_round_trip_multiple_owners(self):
        cfg = SanitizationConfig(
            owner_names=["Mario Rossi", "Anna Bianchi"],
            description_language="it"
        )
        original = "da Mario Rossi per Anna Bianchi"
        sanitized = redact_pii(original, cfg)
        assert "Mario Rossi" not in sanitized
        assert "Anna Bianchi" not in sanitized
        restored = restore_owner_aliases(sanitized, cfg)
        assert "Mario Rossi" in restored
        assert "Anna Bianchi" in restored


# ─────────────────────────────────────────────────────────────────────────────
# assert_sanitized
# ─────────────────────────────────────────────────────────────────────────────

class TestAssertSanitized:
    def test_passes_for_clean_text(self):
        # Should not raise
        assert_sanitized("pagamento supermercato gennaio")

    def test_raises_for_iban(self):
        with pytest.raises(ValueError, match="IBAN"):
            assert_sanitized("bonifico IT60X0542811101000000123456")

    def test_raises_for_pan(self):
        with pytest.raises(ValueError, match="card number"):
            assert_sanitized("addebito 4111 1111 1111 1111")

    def test_passes_after_redaction(self):
        text = redact_pii("IT60X0542811101000000123456 carta 4111 1111 1111 1111")
        # Should not raise — PII was removed
        assert_sanitized(text)


# ─────────────────────────────────────────────────────────────────────────────
# sanitize_dataframe_descriptions (batch helper)
# ─────────────────────────────────────────────────────────────────────────────

class TestSanitizeDataframeDescriptions:
    def test_sanitizes_list_of_strings(self):
        descriptions = [
            "bonifico IT60X0542811101000000123456",
            "pagamento normale",
            "carta 4111 1111 1111 1111",
        ]
        cfg = SanitizationConfig()
        results = sanitize_dataframe_descriptions(descriptions, cfg)
        assert len(results) == 3
        assert "<ACCOUNT_ID>" in results[0]
        assert results[1] == "pagamento normale"
        assert "<CARD_ID>" in results[2]

    def test_empty_list_returns_empty(self):
        assert sanitize_dataframe_descriptions([], None) == []

    def test_preserves_order(self):
        descs = [f"desc {i}" for i in range(10)]
        results = sanitize_dataframe_descriptions(descs, None)
        assert results == descs  # no PII, all unchanged
