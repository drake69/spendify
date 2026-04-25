#!/usr/bin/env python3
"""build_static_rules.py — Generate core/static_rules.json from NSI data.

One-shot script. Run whenever NSI is updated.
Output is committed to git — never regenerated at runtime.

Usage:
    # From extracted NSI tarball directory (v7 format — recommended):
    python scripts/build_static_rules.py --nsi-dir nsi/name-suggestion-index-7.0.20260126
    python scripts/build_static_rules.py --nsi-dir nsi/name-suggestion-index-7.0.20260126 --stats

    # Legacy: from single name-suggestions.min.json (v6 format):
    python scripts/build_static_rules.py --nsi-path nsi/name-suggestions.min.json

    # Other options:
    python scripts/build_static_rules.py --nsi-dir nsi/... --dry-run --stats

Download NSI data:
    mkdir -p nsi
    # Get latest tag from: https://github.com/osmlab/name-suggestion-index/tags
    curl -L https://github.com/osmlab/name-suggestion-index/archive/refs/tags/v7.0.20260126.tar.gz \\
         -o nsi/nsi_v7.tar.gz
    tar -xzf nsi/nsi_v7.tar.gz -C nsi/

Design notes:
  - NO country filter at build time (language != country).
  - Filter ONLY by PFM-relevant OSM categories.
  - 'countries' field preserved for runtime ranking via UserSettings.country.
  - Regex built from brand name + matchNames, robust to bank truncation.
  - Supports both NSI v7 (directory per tag) and v6 (single JSON) formats.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Paths (relative to repo root, i.e. sw_artifacts/)
_REPO_ROOT = Path(__file__).parent.parent
_DEFAULT_NSI_PATH = _REPO_ROOT / "nsi" / "name-suggestions.min.json"
_DEFAULT_NSI_DIR = _REPO_ROOT / "nsi"
_DEFAULT_MAP_PATH = _REPO_ROOT / "core" / "static_rules" / "osm_to_spendifai_map.json"
_DEFAULT_OUTPUT = _REPO_ROOT / "core" / "static_rules.json"

# PFM-relevant OSM tag values per key — only these are included
_PFM_TAGS: dict[str, set[str]] = {
    "shop": {
        "supermarket", "convenience", "bakery", "butcher", "greengrocer",
        "alcohol", "clothing", "shoes", "electronics", "computer",
        "mobile_phone", "furniture", "hardware", "doityourself",
        "sports", "books", "stationery", "chemist", "optician",
        "jewelry", "cosmetics", "pet", "florist", "gift",
    },
    "amenity": {
        "fuel", "fast_food", "restaurant", "cafe", "bar", "pub",
        "pharmacy", "bank", "atm", "cinema", "theatre",
        "gym", "parking", "car_wash", "post_office",
        "hospital", "clinic", "dentist", "veterinary",
    },
    "leisure": {"fitness_centre", "sports_centre", "golf_course"},
    "office": {"insurance", "financial", "travel_agent"},
    "tourism": {"hotel", "hostel", "motel"},
}

# Minimum characters for a prefix pattern WITHOUT end anchor (avoids over-matching)
_MIN_PREFIX_LEN = 5

# Minimum characters for a FULL-WORD pattern WITH \b...\b anchors.
# Shorter names are safe with word boundaries even if unique (e.g. "Lidl", "ENI").
_MIN_FULLWORD_LEN = 3

# Names shorter than this are used in full (with end \b anchor).
# Names equal or longer get a 2/3-length prefix (no end anchor — truncation protection).
# Banks typically truncate names > 10 chars.
_TRUNCATION_THRESHOLD = 10

# Legal suffixes to strip before building regex
_LEGAL_SUFFIXES = [
    " s.p.a.", " spa", " s.r.l.", " srl", " s.a.s.", " sas",
    " ltd.", " ltd", " llc", " inc.", " inc", " corp.",
    " gmbh", " ag", " nv", " bv", " sarl", " sas",
    " s.a.", " sa", " ou", " ab", " oy",
]

# Regex to match qualifier suffixes: " (Something)" or " - Something"
_QUALIFIER_RE = re.compile(r"\s*\([^)]+\)\s*$|\s+-\s+.+$")

# Country/region words that may appear as qualifiers in NSI brand names
# e.g. "Lidl Deutschland", "Lidl España" → base "Lidl"
_COUNTRY_WORDS = frozenset({
    "deutschland", "germany", "france", "españa", "spain", "italia", "italy",
    "uk", "united kingdom", "polska", "netherlands", "belgie", "belgique",
    "schweiz", "suisse", "svizzera", "österreich", "austria", "portugal",
    "turkiye", "türkiye", "russia", "россия", "ukraine", "україна",
    "brasil", "brazil", "mexico", "canada", "australia", "india", "japan",
    "china", "korea", "usa", "america", "europe", "global", "international",
})


def _extract_base_name(display_name: str) -> Optional[str]:
    """Extract a shorter base name from a qualified brand name.

    Examples:
        "Migros (Europe)"    → "Migros"
        "Lidl Deutschland"   → "Lidl"
        "McDonald's France"  → "McDonald's"
        "Centra"             → None (no qualifier)
    Returns None if no qualifier found or base is same as input.
    """
    # Strip parenthetical qualifier: "Migros (Europe)" → "Migros"
    stripped = _QUALIFIER_RE.sub("", display_name).strip()
    if stripped and stripped.lower() != display_name.lower():
        return stripped

    # Try stripping trailing country word(s): "Lidl Deutschland" → "Lidl"
    parts = display_name.split()
    if len(parts) >= 2:
        # Check if last 1-2 words are country qualifiers
        for n_words in (2, 1):
            if len(parts) > n_words:
                tail = " ".join(parts[-n_words:]).lower()
                if tail in _COUNTRY_WORDS:
                    base = " ".join(parts[:-n_words])
                    if base.lower() != display_name.lower():
                        return base
    return None


def _normalize(name: str) -> str:
    """Lowercase + strip legal suffixes + collapse whitespace."""
    result = name.lower().strip()
    for suffix in _LEGAL_SUFFIXES:
        if result.endswith(suffix):
            result = result[: -len(suffix)].strip()
    # Remove punctuation except hyphens and spaces
    result = re.sub(r"[^\w\s\-]", "", result)
    result = re.sub(r"\s+", " ", result).strip()
    return result


def _build_pattern_token(name: str) -> Optional[str]:
    """Return an anchored regex token for name matching.

    For short names (≤ _TRUNCATION_THRESHOLD): full name + \\b end anchor.
      "Centra" → r"\\bcentra\\b"  → matches "centra" alone, NOT "centrale"
    For long names (> threshold): 2/3-length prefix, no end anchor (truncation protection).
      "Esselunga" → r"\\besselu"  → matches "esselu", "esselung", "esselunga"

    The \\b prefix anchor is always added (avoids matching in middle of a word).
    """
    normalized = _normalize(name)
    if len(normalized) < _MIN_FULLWORD_LEN:
        return None
    if len(normalized) <= _TRUNCATION_THRESHOLD:
        # Short/medium name: match full word exactly (word boundaries prevent false positives)
        return r"\b" + re.escape(normalized) + r"\b"
    else:
        # Long name: use prefix for truncation robustness (no end anchor)
        prefix_len = max(_MIN_PREFIX_LEN, len(normalized) * 2 // 3)
        prefix = normalized[:prefix_len]
        return r"\b" + re.escape(prefix)


def _get_osm_tag(tags: dict) -> Optional[tuple[str, str]]:
    """Extract the primary PFM-relevant OSM category tag from a tags dict."""
    for key, values in _PFM_TAGS.items():
        tag_value = tags.get(key)
        if tag_value and tag_value in values:
            return key, tag_value
    return None


def _extract_countries(item: dict) -> list[str]:
    """Extract 2-letter country codes from locationSet.include."""
    location_set = item.get("locationSet", {})
    include = location_set.get("include", [])
    return [
        c.upper()
        for c in include
        if isinstance(c, str) and len(c) == 2 and c.isalpha()
    ]


def _iter_items_from_dir(nsi_dir: Path) -> list[tuple[str, dict]]:
    """Load items from NSI v7 directory structure.

    NSI v7 layout: <nsi_dir>/data/brands/{key}/{value}.json
    Each file has: {"items": [...]}

    Returns list of (osm_tag, item) tuples for PFM-relevant tags only.
    """
    data_dir = nsi_dir / "data" / "brands"
    if not data_dir.exists():
        raise FileNotFoundError(
            f"NSI data directory not found: {data_dir}\n"
            "Expected structure: <nsi_dir>/data/brands/shop/supermarket.json ..."
        )

    result: list[tuple[str, dict]] = []
    for osm_key, osm_values in _PFM_TAGS.items():
        key_dir = data_dir / osm_key
        if not key_dir.exists():
            logger.warning(f"NSI key dir missing: {key_dir}")
            continue
        for osm_value in osm_values:
            json_file = key_dir / f"{osm_value}.json"
            if not json_file.exists():
                logger.debug(f"NSI file not found (skipped): {json_file}")
                continue
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)
            osm_tag = f"{osm_key}={osm_value}"
            for item in data.get("items", []):
                result.append((osm_tag, item))
    logger.info(f"Loaded {len(result)} NSI items from directory {nsi_dir}")
    return result


def _iter_items_from_json(nsi_data: dict) -> list[tuple[str, dict]]:
    """Load items from NSI v6 single-file format.

    Format: {"nsi": {"amenity/fuel": {"items": [...]}, ...}}
    Items have 'tags' with OSM key/value.
    """
    nsi_items_by_category = nsi_data.get("nsi", {})
    result: list[tuple[str, dict]] = []
    for _cat_key, cat_data in nsi_items_by_category.items():
        for item in cat_data.get("items", []):
            tags = item.get("tags", {})
            kv = _get_osm_tag(tags)
            if kv:
                osm_tag = f"{kv[0]}={kv[1]}"
                result.append((osm_tag, item))
    logger.info(f"Loaded {len(result)} NSI items from single JSON")
    return result


def build_rules(
    osm_map: dict,
    *,
    nsi_dir: Optional[Path] = None,
    nsi_data: Optional[dict] = None,
) -> list[dict]:
    """Process NSI items into static rules list.

    Accepts either a directory path (v7) or a pre-loaded dict (v6 JSON).
    """
    if nsi_dir is not None:
        item_pairs = _iter_items_from_dir(nsi_dir)
    elif nsi_data is not None:
        item_pairs = _iter_items_from_json(nsi_data)
    else:
        raise ValueError("Must provide either nsi_dir or nsi_data")

    rules: list[dict] = []
    skipped_no_map = 0
    skipped_short = 0

    for osm_tag, item in item_pairs:
        if osm_tag not in osm_map:
            skipped_no_map += 1
            continue

        display_name = item.get("displayName", "").strip()
        if not display_name:
            continue

        # Build regex tokens from display name + matchNames + base name (if qualified).
        # NOTE: no global deduplication across brands — the same token (e.g. \bmigros\b)
        # can appear in multiple rules. nsi_lookup._rank() picks the best match per country.
        match_names = item.get("matchNames", [])
        base_name = _extract_base_name(display_name)
        all_names = [display_name] + list(match_names)
        if base_name:
            all_names.append(base_name)

        tokens: list[str] = []
        seen_raw_per_brand: set[str] = set()  # dedup within this brand only
        for name in all_names:
            raw = _normalize(name)
            if raw in seen_raw_per_brand:
                continue
            seen_raw_per_brand.add(raw)
            tok = _build_pattern_token(name)
            if tok and tok not in tokens:
                tokens.append(tok)

        if not tokens:
            skipped_short += 1
            continue

        # Longer tokens first (more specific matches win)
        tokens.sort(key=len, reverse=True)
        pattern = "|".join(tokens)

        countries = _extract_countries(item)

        rules.append({
            "pattern": pattern,
            "osm_tag": osm_tag,
            "hint": osm_map[osm_tag]["hint"],
            "brand": display_name,
            "countries": countries,
            "source": "nsi",
        })

    logger.info(
        f"Rules built: {len(rules)} | "
        f"Skipped (no map): {skipped_no_map} | "
        f"Skipped (too short): {skipped_short}"
    )
    return rules


def _find_nsi_dir(base: Path) -> Optional[Path]:
    """Auto-detect extracted NSI directory inside base (e.g. nsi/)."""
    if (base / "data" / "brands").exists():
        return base
    # Look for a single extracted subdirectory like name-suggestion-index-7.0.xxx
    subdirs = [p for p in base.iterdir() if p.is_dir() and "name-suggestion" in p.name]
    if len(subdirs) == 1:
        return subdirs[0]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate core/static_rules.json from NSI data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    nsi_group = parser.add_mutually_exclusive_group()
    nsi_group.add_argument(
        "--nsi-dir",
        type=Path,
        default=None,
        help="Path to extracted NSI directory (v7 format, recommended)",
    )
    nsi_group.add_argument(
        "--nsi-path",
        type=Path,
        default=None,
        help="Path to NSI name-suggestions.min.json (v6 single-file format)",
    )
    parser.add_argument(
        "--map-path",
        type=Path,
        default=_DEFAULT_MAP_PATH,
        help=f"Path to osm_to_spendifai_map.json (default: {_DEFAULT_MAP_PATH})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help=f"Output path (default: {_DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Process but do not write output file",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print country and tag distribution after processing",
    )
    args = parser.parse_args()

    # Auto-detect NSI source if neither flag given
    nsi_dir: Optional[Path] = None
    nsi_data: Optional[dict] = None

    if args.nsi_dir is not None:
        nsi_dir = args.nsi_dir
    elif args.nsi_path is not None:
        pass  # use nsi_path below
    else:
        # Try auto-detect: look for extracted directory in nsi/
        detected = _find_nsi_dir(_DEFAULT_NSI_DIR)
        if detected:
            logger.info(f"Auto-detected NSI directory: {detected}")
            nsi_dir = detected
        elif _DEFAULT_NSI_PATH.exists():
            logger.info(f"Auto-detected NSI single file: {_DEFAULT_NSI_PATH}")
            args.nsi_path = _DEFAULT_NSI_PATH
        else:
            logger.error("No NSI data found. Provide --nsi-dir or --nsi-path.")
            logger.error("Download with:")
            logger.error("  mkdir -p nsi")
            logger.error(
                "  curl -L https://github.com/osmlab/name-suggestion-index/archive/"
                "refs/tags/v7.0.20260126.tar.gz -o nsi/nsi_v7.tar.gz"
            )
            logger.error("  tar -xzf nsi/nsi_v7.tar.gz -C nsi/")
            sys.exit(1)

    if not args.map_path.exists():
        logger.error(f"OSM map not found: {args.map_path}")
        sys.exit(1)

    # Load NSI data (v6 path only; v7 dir loaded lazily by build_rules)
    if args.nsi_path is not None:
        logger.info(f"Loading NSI from {args.nsi_path} ...")
        with open(args.nsi_path, encoding="utf-8") as f:
            nsi_data = json.load(f)

    with open(args.map_path, encoding="utf-8") as f:
        osm_map = json.load(f)
    # Remove the _comment key if present
    osm_map.pop("_comment", None)

    # Build rules
    rules = build_rules(osm_map, nsi_dir=nsi_dir, nsi_data=nsi_data)
    rules.sort(key=lambda r: r["brand"].lower())

    # Stats
    if args.stats:
        country_counts: dict[str, int] = {}
        tag_counts: dict[str, int] = {}
        for rule in rules:
            for c in rule["countries"]:
                country_counts[c] = country_counts.get(c, 0) + 1
            tag_counts[rule["osm_tag"]] = tag_counts.get(rule["osm_tag"], 0) + 1
        top_countries = sorted(country_counts.items(), key=lambda x: -x[1])[:15]
        top_tags = sorted(tag_counts.items(), key=lambda x: -x[1])[:15]
        print(f"\nTotal rules: {len(rules)}")
        print(f"Top countries: {top_countries}")
        print(f"Top tags: {top_tags}")

    if args.dry_run:
        logger.info(f"Dry run — would write {len(rules)} rules to {args.output}")
        return

    # Write output
    output = {
        "version": "1.0",
        "source": "nsi",
        "nsi_url": "https://github.com/osmlab/name-suggestion-index",
        "license": "ISC",
        "generated_by": "scripts/build_static_rules.py",
        "note": (
            "No country filter at build time. "
            "Use UserSettings.country (ISO 3166-1 alpha-2) for runtime ranking. "
            "Language != country."
        ),
        "rules": rules,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    logger.info(f"Written {len(rules)} rules to {args.output}")


if __name__ == "__main__":
    main()
