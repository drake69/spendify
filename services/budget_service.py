"""BudgetService — budget targets and actual-vs-budget comparison (A-02)."""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal

from sqlalchemy.orm import sessionmaker

from db import repository


class BudgetService:
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

    # ── Budget targets ────────────────────────────────────────────────────────

    def get_targets(self) -> list[dict]:
        """Return all budget targets as list of dicts {category, target_pct, id}."""
        with self._session() as s:
            rows = repository.get_budget_targets(s)
            return [
                {
                    "id": r.id,
                    "category": r.category,
                    "target_pct": float(r.target_pct),
                }
                for r in rows
            ]

    def save_targets(self, targets: list[dict]) -> None:
        """Bulk-save budget targets.

        *targets* is a list of {category: str, target_pct: float}.
        Categories with target_pct == 0 or None are deleted.
        """
        with self._session() as s:
            existing = {bt.category: bt for bt in repository.get_budget_targets(s)}
            seen_categories: set[str] = set()

            for t in targets:
                cat = t["category"]
                pct = t.get("target_pct")
                seen_categories.add(cat)

                if pct and float(pct) > 0:
                    repository.upsert_budget_target(s, cat, Decimal(str(pct)))
                elif cat in existing:
                    repository.delete_budget_target(s, existing[cat].id)

            # Remove targets for categories no longer in the list
            for cat, bt in existing.items():
                if cat not in seen_categories:
                    repository.delete_budget_target(s, bt.id)

            s.commit()

    # ── Actual vs Budget ──────────────────────────────────────────────────────

    def get_actual_vs_budget(self, date_from: str, date_to: str) -> dict:
        """Compare actual spending vs budget targets for the given period.

        Returns dict with:
            total_income, total_expenses, liquidity,
            rows: list of {category, target_pct, actual_pct, actual_amount, deviation, status}
            liquidity_target_pct: 100 - sum(target_pct)
            liquidity_actual_pct: 100 - sum(actual_expense_pct)
        """
        with self._session() as s:
            targets = {bt.category: float(bt.target_pct) for bt in repository.get_budget_targets(s)}
            period = repository.get_period_totals(s, date_from, date_to)

        total_income = period["total_income"]
        total_expenses = period["total_expenses"]
        liquidity = total_income - total_expenses
        by_category = period["by_category"]

        # Build category rows
        rows = []
        all_cats: set[str] = set(targets.keys())
        for item in by_category:
            all_cats.add(item["category"])

        cat_amounts = {item["category"]: item["amount"] for item in by_category}

        for cat in sorted(all_cats):
            target_pct = targets.get(cat)
            actual_amount = cat_amounts.get(cat, 0.0)
            actual_pct = (actual_amount / total_expenses * 100) if total_expenses > 0 else 0.0

            if target_pct is not None:
                deviation = actual_pct - target_pct
                abs_dev = abs(deviation)
                if abs_dev <= 5:
                    status = "green"
                elif abs_dev <= 10:
                    status = "yellow"
                else:
                    status = "red"
            else:
                deviation = None
                status = "none"

            rows.append({
                "category": cat,
                "target_pct": target_pct,
                "actual_pct": round(actual_pct, 1),
                "actual_amount": actual_amount,
                "deviation": round(deviation, 1) if deviation is not None else None,
                "status": status,
            })

        # Also add categories that have actuals but no target
        # (already handled above since we merged all_cats)

        # Liquidity metrics
        total_target_pct = sum(targets.values())
        liquidity_target_pct = 100.0 - total_target_pct
        total_actual_expense_pct = (total_expenses / total_income * 100) if total_income > 0 else 0.0
        liquidity_actual_pct = 100.0 - total_actual_expense_pct

        return {
            "total_income": total_income,
            "total_expenses": total_expenses,
            "liquidity": liquidity,
            "liquidity_target_pct": round(liquidity_target_pct, 1),
            "liquidity_actual_pct": round(liquidity_actual_pct, 1),
            "rows": rows,
        }
