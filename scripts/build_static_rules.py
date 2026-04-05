#!/usr/bin/env python3
"""build_static_rules.py — Generate core/static_rules.json from NSI data.

One-shot script. Run whenever NSI is updated.
Output is committed to git — never regenerated at runtime.

Usage:
    python scripts/build_static_rules.py
    python scripts/build_static_rules.py --nsi-path nsi/name-suggestions.min.json
    python scripts/build_static_rules.py --dry-run --stats

Download NSI data first:
    mkdir -p nsi
    curl -L https://github.com/osmlab/name-suggestion-index/releases/latest/download/name-suggestions.min.json \\
         -o nsi/name-suggestions.min.json

Or as git submodule:
    git submodule add https://github.com/osmlab/name-suggestion-index.git nsi

Design notes:
  - NO country filter at build time (language != country).
  - Filter ONLY by PFM-relevant OSM categories.
  - 'countries' field preserved for runtime ranking via UserSettings.country.
  - Regex built from brand name + matchNames, robust to bank truncation.
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

# Minimum characters for a valid regex prefix (avoids over-matching)
_MIN_PREFIX_LEN = 5

# Legal suffixes to strip before building regex
_LEGAL_SUFFIXES = [
    " s.p.a.", " spa", " s.r.l.", " srl", " s.a.s.", " sas",
    " ltd.", " ltd", " llc", " inc.", " inc", " corp.",
    " gmbh", " ag", " nv", " bv", " sarl", " sas",
    " s.a.", " sa", " ou", " ab", " oy",
]


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


def _build_prefix(name: str) -> Optional[str]:
    """Return a truncation-robust prefix for regex matching."""
    normalized = _normalize(name)
    if len(normalized) < _MIN_PREFIX_LEN:
        return None
    # Use enough chars so truncated bank names still match
    # "esselunga" → "esselun" (7 chars — handles "ESSELUN" truncation)
    prefix_len = max(_MIN_PREFIX_LEN, min(len(normalized), len(normalized) * 2 // 3))
    return normalized[:prefix_len]


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


def build_rules(nsi_data: dict, osm_map: dict) -> list[dict]:
    """Process NSI data into static rules list."""
    # NSI format: {"nsi": {"amenity/fuel": {"items": [...]}}}
    nsi_items_by_category = nsi_data.get("nsi", {})

    rules: list[dict] = []
    seen_prefixes: set[str] = set()
    skipped_no_map = 0
    skipped_short = 0
    skipped_dup = 0

    for _cat_key, cat_data in nsi_items_by_category.items():
        for item in cat_data.get("items", []):
            tags = item.get("tags", {})
            kv = _get_osm_tag(tags)
            if not kv:
                continue

            osm_key, osm_value = kv
            osm_tag = f"{osm_key}={osm_value}"

            if osm_tag not in osm_map:
                skipped_no_map += 1
                continue

            display_name = item.get("displayName", "").strip()
            if not display_name:
                continue

            # Build regex from display name + matchNames
            match_names = item.get("matchNames", [])
            all_names = [display_name] + list(match_names)

            prefixes: list[str] = []
            for name in all_names:
                p = _build_prefix(name)
                if p and p not in seen_prefixes:
                    prefixes.append(p)
                    seen_prefixes.add(p)

            if not prefixes:
                skipped_short += 1
                continue

            # Longest prefix first for greedier matching
            prefixes.sort(key=len, reverse=True)
            pattern = "|".join(re.escape(p) for p in prefixes)

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
        f"Skipped (too short): {skipped_short} | "
        f"Skipped (dup prefix): {skipped_dup}"
    )
    return rules


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate core/static_rules.json from NSI data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--nsi-path",
        type=Path,
        default=_DEFAULT_NSI_PATH,
        help=f"Path to NSI name-suggestions.min.json (default: {_DEFAULT_NSI_PATH})",
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

    # Validate inputs
    if not args.nsi_path.exists():
        logger.error(f"NSI file not found: {args.nsi_path}")
        logger.error("Download it with:")
        logger.error("  mkdir -p nsi")
        logger.error(
            "  curl -L https://github.com/osmlab/name-suggestion-index/releases/latest/download/name-suggestions.min.json"
            f" -o {args.nsi_path}"
        )
        sys.exit(1)

    if not args.map_path.exists():
        logger.error(f"OSM map not found: {args.map_path}")
        sys.exit(1)

    # Load inputs
    logger.info(f"Loading NSI from {args.nsi_path} ...")
    with open(args.nsi_path, encoding="utf-8") as f:
        nsi_data = json.load(f)

    with open(args.map_path, encoding="utf-8") as f:
        osm_map = json.load(f)
    # Remove the _comment key if present
    osm_map.pop("_comment", None)

    # Build rules
    rules = build_rules(nsi_data, osm_map)
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
