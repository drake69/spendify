"""NsiTaxonomyService — build and cache the OSM tag → taxonomy mapping (C-08-cascade).

The mapping is stored in `nsi_tag_mapping` table and rebuilt when:
  - the table is empty (first run after DB migration), or
  - the user taxonomy has changed (SHA-256 hash mismatch).

Build strategy:
  1. LLM call (if backend available): maps all known OSM tags to user's current taxonomy.
  2. Static fallback: parse `osm_to_spendifai_map.json` hints (Category > Subcategory)
     and validate against taxonomy for any tags not covered by the LLM.

Usage:
    svc = NsiTaxonomyService(engine)
    with svc._session() as s:
        taxonomy_map = svc.get_or_build(s, taxonomy, llm_backend)
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import sessionmaker

from core.categorizer import TaxonomyConfig
from support.logging import setup_logging

logger = setup_logging()

_OSM_MAP_PATH = Path(__file__).parent.parent / "core" / "static_rules" / "osm_to_spendifai_map.json"
_STATIC_RULES_PATH = Path(__file__).parent.parent / "core" / "static_rules.json"

# JSON schema for the LLM mapping response
_MAPPING_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "mappings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "osm_tag":    {"type": "string"},
                    "category":   {"type": "string"},
                    "subcategory": {"type": "string"},
                },
                "required": ["osm_tag", "category", "subcategory"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["mappings"],
    "additionalProperties": False,
}


class NsiTaxonomyService:
    def __init__(self, engine) -> None:
        self.engine = engine
        self._Session = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def _session(self):
        s = self._Session()
        try:
            yield s
        finally:
            s.close()

    # ── Public API ─────────────────────────────────────────────────────────────

    @staticmethod
    def compute_taxonomy_hash(taxonomy: TaxonomyConfig) -> str:
        """SHA-256 of sorted taxonomy JSON (language-agnostic fingerprint)."""
        data = {
            "expenses": {k: sorted(v) for k, v in sorted(taxonomy.expenses.items())},
            "income":   {k: sorted(v) for k, v in sorted(taxonomy.income.items())},
        }
        return hashlib.sha256(
            json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

    def needs_rebuild(self, session, current_hash: str) -> bool:
        """True if nsi_tag_mapping is empty or has a stale taxonomy_hash."""
        from db.repository import get_nsi_tag_mapping_hash
        stored = get_nsi_tag_mapping_hash(session)
        return stored != current_hash

    def get_or_build(
        self,
        session,
        taxonomy: TaxonomyConfig,
        llm_backend=None,
    ) -> dict[str, tuple[str, str]]:
        """Return taxonomy_map from DB, rebuilding if stale or empty."""
        from db.repository import get_nsi_tag_mapping
        current_hash = self.compute_taxonomy_hash(taxonomy)
        if self.needs_rebuild(session, current_hash):
            logger.info("NsiTaxonomyService: taxonomy_map stale or empty — rebuilding")
            return self.build(session, taxonomy, llm_backend)
        return get_nsi_tag_mapping(session)

    def build(
        self,
        session,
        taxonomy: TaxonomyConfig,
        llm_backend=None,
    ) -> dict[str, tuple[str, str]]:
        """Build taxonomy_map, persist to DB, return the result.

        Order of resolution:
          1. LLM call (if backend available)
          2. Static parse from osm_to_spendifai_map.json (for remaining tags)
        """
        from db.repository import clear_nsi_tag_mapping, upsert_nsi_tag_mapping_bulk

        taxonomy_hash = self.compute_taxonomy_hash(taxonomy)
        all_tags = self._collect_osm_tags()

        result: dict[str, tuple[str, str]] = {}

        # 1. LLM mapping
        if llm_backend is not None and all_tags:
            llm_result = self._llm_map(all_tags, taxonomy, llm_backend)
            result.update(llm_result)
            logger.info(f"NsiTaxonomyService: LLM mapped {len(llm_result)}/{len(all_tags)} tags")

        # 2. Static fallback for any unmapped tags
        static_result = self._static_map(all_tags, taxonomy)
        filled = 0
        for tag, pair in static_result.items():
            if tag not in result:
                result[tag] = pair
                filled += 1
        if filled:
            logger.info(f"NsiTaxonomyService: static fallback filled {filled} additional tags")

        # Persist
        clear_nsi_tag_mapping(session)
        now = datetime.now(timezone.utc)
        rows = [
            {
                "osm_tag": tag,
                "category": cat,
                "subcategory": sub,
                "taxonomy_hash": taxonomy_hash,
                "updated_at": now,
            }
            for tag, (cat, sub) in result.items()
        ]
        if rows:
            upsert_nsi_tag_mapping_bulk(session, rows)
        session.commit()

        logger.info(
            f"NsiTaxonomyService.build: {len(result)} OSM tags mapped "
            f"(hash={taxonomy_hash[:8]}…)"
        )
        return result

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _collect_osm_tags(self) -> list[str]:
        """Collect unique OSM tags from static_rules.json and osm_to_spendifai_map.json."""
        tags: set[str] = set()
        if _STATIC_RULES_PATH.exists():
            try:
                data = json.loads(_STATIC_RULES_PATH.read_text(encoding="utf-8"))
                for rule in data.get("rules", []):
                    tag = rule.get("osm_tag", "")
                    if tag:
                        tags.add(tag)
            except Exception as exc:
                logger.warning(f"NsiTaxonomyService: could not read static_rules.json: {exc}")
        if _OSM_MAP_PATH.exists():
            try:
                data = json.loads(_OSM_MAP_PATH.read_text(encoding="utf-8"))
                for tag in data:
                    if not tag.startswith("_"):
                        tags.add(tag)
            except Exception as exc:
                logger.warning(f"NsiTaxonomyService: could not read osm_to_spendifai_map.json: {exc}")
        return sorted(tags)

    def _static_map(
        self, osm_tags: list[str], taxonomy: TaxonomyConfig
    ) -> dict[str, tuple[str, str]]:
        """Parse osm_to_spendifai_map.json hints and validate against taxonomy."""
        result: dict[str, tuple[str, str]] = {}
        if not _OSM_MAP_PATH.exists():
            return result
        try:
            data = json.loads(_OSM_MAP_PATH.read_text(encoding="utf-8"))
        except Exception:
            return result
        for tag in osm_tags:
            entry = data.get(tag)
            if not entry:
                continue
            hint = entry.get("hint", "")
            if " > " not in hint:
                continue
            cat, sub = (p.strip() for p in hint.split(" > ", 1))
            if taxonomy.is_valid_pair(cat, sub):
                result[tag] = (cat, sub)
        return result

    def _llm_map(
        self,
        osm_tags: list[str],
        taxonomy: TaxonomyConfig,
        llm_backend,
    ) -> dict[str, tuple[str, str]]:
        """Single LLM call: map all OSM tags to (category, subcategory) pairs."""
        taxonomy_text = "Spese:\n" + "\n".join(
            f"  {cat}: {', '.join(subs)}" for cat, subs in taxonomy.expenses.items()
        ) + "\nEntrate:\n" + "\n".join(
            f"  {cat}: {', '.join(subs)}" for cat, subs in taxonomy.income.items()
        )
        system_prompt = (
            "Sei un assistente che mappa tag OSM (OpenStreetMap) a categorie di spesa "
            "per un'app di finanza personale. Rispondi SOLO con JSON valido."
        )
        user_prompt = (
            "Mappa ciascun tag OSM alla categoria e sottocategoria più appropriate "
            "dalla tassonomia fornita. Se un tag non corrisponde a nessuna voce, "
            "omettilo dall'output.\n\n"
            f"Tag OSM da mappare:\n{json.dumps(osm_tags, ensure_ascii=False)}\n\n"
            f"Tassonomia disponibile:\n{taxonomy_text}\n\n"
            "Rispondi con un oggetto JSON con chiave 'mappings', array di oggetti "
            "{osm_tag, category, subcategory}."
        )
        try:
            response = llm_backend.complete_structured(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                json_schema=_MAPPING_SCHEMA,
                temperature=0.0,
            )
            mappings = response.get("mappings", [])
        except Exception as exc:
            logger.warning(f"NsiTaxonomyService._llm_map: LLM call failed: {exc}")
            return {}

        result: dict[str, tuple[str, str]] = {}
        for item in mappings:
            tag = item.get("osm_tag", "")
            cat = item.get("category", "")
            sub = item.get("subcategory", "")
            if tag and cat and sub and taxonomy.is_valid_pair(cat, sub):
                result[tag] = (cat, sub)
            elif tag and cat and sub:
                logger.debug(
                    f"NsiTaxonomyService: LLM returned invalid pair for {tag!r}: "
                    f"({cat!r}, {sub!r}) — discarded"
                )
        return result
