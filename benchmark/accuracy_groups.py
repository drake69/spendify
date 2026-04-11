"""
accuracy_groups.py — Mappa commit → gruppo di equivalenza accuratezza.

Due commit nello stesso gruppo producono risultati di accuratezza identici
(le differenze tra loro sono solo infra, docs, CI, performance).

Usato da:
- benchmark_stats.py (--by-group)
- benchmark_classifier.py / benchmark_categorizer.py (resume cross-commit)

Aggiornare ACCURACY_GROUPS e _GROUP_COMMITS quando si aggiunge un nuovo
accuracy boundary (commit che modifica core/categorizer.py, core/classifier.py,
core/description_cleaner.py, core/normalizer.py, core/sanitizer.py, core/nsi_lookup.py).
"""
from __future__ import annotations

# Ordine: dal più recente al più vecchio.
# (group_id, boundary_commit, descrizione)
ACCURACY_GROUPS = [
    ("G6", "b3947a7", "real taxonomy_map + fix SyntheticHistoryCache"),
    ("G5", "c4a6768", "static rules JSON + NSI lookup"),
    ("G4", "4e912f8", "cleaner dedup + indexed batch"),
    ("G3", "5ffe1aa", "enforce account_type (BUG-08)"),
    ("G2", "03fdeec", "9 fix pipeline: Phase 0, sign conv, rarity"),
    ("G1", "44b973f", "multi-step classifier"),
    ("G0", "d231f57", "pre-refactor"),
]

# Commit espliciti per ogni gruppo (short hash 7 char).
# Commit non elencati → G0 (fallback).
_GROUP_COMMITS: dict[str, str] = {}

for _c in [
    "78ec3e8", "164bcac", "6eaba1f", "4bca4b4", "c3630c5",
    "0095d1a", "351110d", "b4e5163", "a5a007b", "8a23584",
    "27a0093", "38f28f5", "86d7d51", "f1fb42c", "c15e2ca",
    "2122c67", "42c0d41", "73bfb86", "bb1ab7d", "bd0875f",
    "61e6024", "8e7b6ff", "b3947a7",
]:
    _GROUP_COMMITS[_c] = "G6"

for _c in [
    "c24bb62", "a8f9fb7", "ec32c65", "ac7754c", "3549f07",
    "6ee026b", "e5b472a", "cd6809a", "3e7e9b8", "78af418",
    "75f990b", "1d97465", "8412d5e", "a710159", "b105d86",
    "c4a6768", "7b1e3ca",
]:
    _GROUP_COMMITS[_c] = "G5"

for _c in ["4e912f8", "398c56e"]:
    _GROUP_COMMITS[_c] = "G4"

for _c in ["5ffe1aa"]:
    _GROUP_COMMITS[_c] = "G3"

for _c in [
    "03fdeec", "9ee6845", "46d315a", "df44f11", "9da074b",
    "89f5032", "4ac9c52", "7988657", "1f1a2a0", "468476b",
    "560cae7",
]:
    _GROUP_COMMITS[_c] = "G2"

for _c in ["44b973f", "da59833", "59e6944"]:
    _GROUP_COMMITS[_c] = "G1"


class UnknownCommitError(ValueError):
    """Commit non presente nella tabella di equivalenza."""
    pass


def commit_to_group(commit: str, *, strict: bool = True) -> str:
    """Mappa un commit hash (short 7 char) al suo gruppo di equivalenza.

    Args:
        commit: short hash (7 char) del commit.
        strict: se True (default), lancia UnknownCommitError per commit
                non censiti. Se False, ritorna "UNKNOWN".

    Raises:
        UnknownCommitError: se strict=True e il commit non è nella tabella.
    """
    group = _GROUP_COMMITS.get(commit)
    if group is not None:
        return group
    if strict:
        raise UnknownCommitError(
            f"Commit '{commit}' non presente nella tabella di equivalenza "
            f"(benchmark/accuracy_groups.py). Aggiorna _GROUP_COMMITS prima "
            f"di lanciare il benchmark."
        )
    return "UNKNOWN"
