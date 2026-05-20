"""Tests for NsiTaxonomyService — OSM tag → taxonomy mapping.

The service already had high coverage thanks to the prewarm path tests
in tests/test_service_category.py. These cases target the still-missing
branches: compute_taxonomy_hash, _collect_osm_tags, _static_map and
_llm_map happy / error paths.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine

from core.categorizer import TaxonomyConfig
from db.models import create_tables
from services.nsi_taxonomy_service import NsiTaxonomyService


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    return eng


@pytest.fixture
def svc(engine):
    return NsiTaxonomyService(engine)


@pytest.fixture
def taxonomy():
    return TaxonomyConfig(
        expenses={"Alimentari": ["Spesa supermercato", "Ristoranti"]},
        income={"Lavoro": ["Stipendio"]},
    )


# ── compute_taxonomy_hash ─────────────────────────────────────────────────────

class TestComputeTaxonomyHash:

    def test_returns_stable_hex_digest(self, taxonomy):
        h1 = NsiTaxonomyService.compute_taxonomy_hash(taxonomy)
        h2 = NsiTaxonomyService.compute_taxonomy_hash(taxonomy)
        assert h1 == h2
        # 256-bit SHA → 64 hex chars
        assert len(h1) == 64
        assert all(c in "0123456789abcdef" for c in h1)

    def test_different_taxonomies_have_different_hashes(self, taxonomy):
        other = TaxonomyConfig(
            expenses={"Alimentari": ["Spesa supermercato"]},  # one sub less
            income={"Lavoro": ["Stipendio"]},
        )
        assert NsiTaxonomyService.compute_taxonomy_hash(taxonomy) != \
               NsiTaxonomyService.compute_taxonomy_hash(other)


# ── _collect_osm_tags ─────────────────────────────────────────────────────────

class TestCollectOsmTags:

    def test_returns_non_empty_sorted_list(self, svc):
        """The bundled static_rules.json + osm_to_spendifai_map.json contain
        real tags; the helper must return a non-empty sorted list."""
        tags = svc._collect_osm_tags()
        assert isinstance(tags, list)
        assert len(tags) > 0
        assert tags == sorted(tags)
        # Tags starting with "_" must be filtered (they are meta-entries
        # in osm_to_spendifai_map.json like "_comment")
        assert not any(t.startswith("_") for t in tags)


# ── _static_map ───────────────────────────────────────────────────────────────

class TestStaticMap:

    def test_unknown_tag_is_not_mapped(self, svc, taxonomy):
        """A tag the bundled JSON does not know → not in the result."""
        out = svc._static_map(["this-tag-does-not-exist-anywhere"], taxonomy)
        assert out == {}

    def test_returns_empty_when_taxonomy_does_not_validate(self, svc):
        """Static map yields a hint, but the (cat, sub) pair is not valid
        in the user-provided taxonomy → discarded."""
        # Find a known tag from the bundled JSON to feed in
        tags = svc._collect_osm_tags()
        assert tags  # sanity
        # An intentionally narrow taxonomy that can't match any OSM hint
        narrow = TaxonomyConfig(
            expenses={"NoneOfYourMaps": ["Nothing"]},
            income={},
        )
        out = svc._static_map(tags[:5], narrow)
        # Either empty (most likely) or all keys pass the validator, but
        # never raises and never returns invalid pairs.
        for tag, (cat, sub) in out.items():
            assert narrow.is_valid_pair(cat, sub)


# ── _llm_map ──────────────────────────────────────────────────────────────────

class TestLlmMap:

    def test_happy_path_keeps_valid_pairs(self, svc, taxonomy):
        """LLM returns mixed valid/invalid pairs — the valid ones survive,
        the rest are silently dropped."""
        backend = MagicMock()
        backend.complete_structured.return_value = {
            "mappings": [
                # Valid: matches taxonomy
                {"osm_tag": "shop=supermarket", "category": "Alimentari",
                 "subcategory": "Spesa supermercato"},
                # Invalid: subcategory not in taxonomy → discarded
                {"osm_tag": "shop=mall", "category": "Alimentari",
                 "subcategory": "DoesNotExist"},
                # Invalid: missing fields → discarded
                {"osm_tag": "", "category": "Alimentari", "subcategory": "Ristoranti"},
            ]
        }
        out = svc._llm_map(["shop=supermarket", "shop=mall"], taxonomy, backend)
        assert out == {"shop=supermarket": ("Alimentari", "Spesa supermercato")}
        backend.complete_structured.assert_called_once()

    def test_backend_failure_returns_empty(self, svc, taxonomy):
        """LLM call raises → caller gets {} and no exception bubbles up."""
        backend = MagicMock()
        backend.complete_structured.side_effect = RuntimeError("synthetic")
        out = svc._llm_map(["shop=supermarket"], taxonomy, backend)
        assert out == {}

    def test_empty_mappings_returns_empty(self, svc, taxonomy):
        """LLM responds with mappings=[] → returns empty dict cleanly."""
        backend = MagicMock()
        backend.complete_structured.return_value = {"mappings": []}
        out = svc._llm_map(["shop=supermarket"], taxonomy, backend)
        assert out == {}


# ── needs_rebuild ─────────────────────────────────────────────────────────────

class TestNeedsRebuild:

    def test_empty_db_needs_rebuild(self, engine, svc, taxonomy):
        """Fresh DB → no stored hash → needs_rebuild is True."""
        from db.models import get_session
        h = NsiTaxonomyService.compute_taxonomy_hash(taxonomy)
        with get_session(engine) as s:
            assert svc.needs_rebuild(s, h) is True

    def test_matching_hash_does_not_need_rebuild(self, engine, svc, taxonomy):
        """After a build, the stored hash matches → no rebuild needed."""
        from db.models import get_session

        backend = MagicMock()
        backend.complete_structured.return_value = {"mappings": []}
        with get_session(engine) as s:
            svc.build(s, taxonomy, llm_backend=backend)
        h = NsiTaxonomyService.compute_taxonomy_hash(taxonomy)
        with get_session(engine) as s:
            assert svc.needs_rebuild(s, h) is False
