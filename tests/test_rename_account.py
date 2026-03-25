"""Tests for account rename with tx_id recalculation and updated_at behaviour."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text as _text

from core.normalizer import compute_transaction_id
from db.models import (
    Account,
    Base,
    DocumentSchemaModel,
    InternalTransferLink,
    ReconciliationLink,
    Transaction,
    get_session,
)
from db.repository import rename_account


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    # Disable FK enforcement so raw SQL PK updates don't fail mid-cascade
    from sqlalchemy import event
    @event.listens_for(eng, "connect")
    def _set_sqlite_pragma(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA foreign_keys=OFF")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    with get_session(engine) as s:
        yield s


def _make_tx_id(date: str, amount: str, desc: str, account_label: str, source_file: str = "f.csv") -> str:
    """Compute a tx_id the same way the orchestrator does."""
    return compute_transaction_id(source_file, date, amount, desc, account_label=account_label)


def _insert_tx(
    session, *, account_label: str, date: str = "2025-01-15",
    amount: float = -42.50, raw_description: str = "PAGAMENTO COOP",
    source_file: str = "f.csv",
) -> Transaction:
    """Insert a transaction whose id is computed consistently with the normalizer."""
    amount_key = str(Decimal(str(amount)).normalize())
    desc_key = raw_description.strip()
    tx_id = _make_tx_id(date, amount_key, desc_key, account_label, source_file)
    t = Transaction(
        id=tx_id,
        date=date,
        amount=amount,
        currency="EUR",
        description=raw_description.lower(),
        raw_description=raw_description,
        raw_amount=str(amount),
        source_file=source_file,
        account_label=account_label,
        tx_type="expense",
    )
    session.add(t)
    session.flush()
    return t


def _insert_account(session, name: str, bank_name: str = "TestBank") -> Account:
    acc = Account(name=name, bank_name=bank_name)
    session.add(acc)
    session.flush()
    return acc


# ── Transaction ID recalculation ─────────────────────────────────────────────

class TestRenameAccountRecalculation:
    """Verify tx_id is recomputed when account_label changes."""

    def test_tx_id_changes_on_rename(self, session):
        acc = _insert_account(session, "OldAccount")
        tx = _insert_tx(session, account_label="OldAccount")
        old_id = tx.id

        # Compute expected new id
        amount_key = str(Decimal(str(tx.amount)).normalize())
        desc_key = tx.raw_description.strip()
        expected_new_id = _make_tx_id(tx.date, amount_key, desc_key, "NewAccount", tx.source_file)

        count = rename_account(session, acc.id, "NewAccount")
        session.flush()

        assert count == 1
        assert old_id != expected_new_id  # sanity: ids should differ
        # The old id should no longer exist
        assert session.get(Transaction, old_id) is None
        # The new id should exist with updated account_label
        new_tx = session.get(Transaction, expected_new_id)
        assert new_tx is not None
        assert new_tx.account_label == "NewAccount"

    def test_multiple_transactions_all_updated(self, session):
        acc = _insert_account(session, "Conto1")
        tx1 = _insert_tx(session, account_label="Conto1", date="2025-01-01", raw_description="DESC A")
        tx2 = _insert_tx(session, account_label="Conto1", date="2025-02-01", raw_description="DESC B")
        old_ids = {tx1.id, tx2.id}

        count = rename_account(session, acc.id, "Conto2")
        session.flush()

        assert count == 2
        # Old IDs should be gone
        for oid in old_ids:
            assert session.get(Transaction, oid) is None
        # All remaining transactions for this account should have new label
        remaining = session.query(Transaction).filter(Transaction.account_label == "Conto2").all()
        assert len(remaining) == 2

    def test_no_op_when_name_unchanged(self, session):
        acc = _insert_account(session, "SameName")
        tx = _insert_tx(session, account_label="SameName")
        old_id = tx.id

        count = rename_account(session, acc.id, "SameName")
        assert count == 0
        # Transaction should be unchanged
        assert session.get(Transaction, old_id) is not None

    def test_account_not_found(self, session):
        result = rename_account(session, 9999, "Whatever")
        assert result == -1

    def test_duplicate_name_raises(self, session):
        _insert_account(session, "AccountA")
        acc_b = _insert_account(session, "AccountB")
        with pytest.raises(ValueError, match="già esistente"):
            rename_account(session, acc_b.id, "AccountA")


# ── FK cascade to related tables ─────────────────────────────────────────────

class TestRenameAccountFKCascade:
    """Verify reconciliation_link and internal_transfer_link FKs are updated."""

    def test_reconciliation_link_updated(self, session):
        acc = _insert_account(session, "AccR")
        tx1 = _insert_tx(session, account_label="AccR", date="2025-03-01", raw_description="SETTLE")
        tx2 = _insert_tx(session, account_label="AccR", date="2025-03-01", raw_description="DETAIL")
        link = ReconciliationLink(settlement_id=tx1.id, detail_id=tx2.id, delta=0, method="auto")
        session.add(link)
        session.flush()

        rename_account(session, acc.id, "AccR_New")
        session.flush()
        session.expire_all()  # force reload from DB after raw SQL updates

        # Compute expected new ids
        for orig_tx, desc in [(tx1, "SETTLE"), (tx2, "DETAIL")]:
            amount_key = str(Decimal(str(orig_tx.amount)).normalize())
            expected = _make_tx_id(orig_tx.date, amount_key, desc.strip(), "AccR_New", orig_tx.source_file)
            assert session.get(Transaction, expected) is not None

        updated_link = session.query(ReconciliationLink).filter_by(id=link.id).one()
        # FKs should point to the new ids
        assert updated_link.settlement_id != tx1.id or tx1.id == updated_link.settlement_id
        # More precise: they should match the new computed ids
        new_tx1_id = _make_tx_id(
            tx1.date,
            str(Decimal(str(tx1.amount)).normalize()),
            "SETTLE",
            "AccR_New",
            tx1.source_file,
        )
        new_tx2_id = _make_tx_id(
            tx2.date,
            str(Decimal(str(tx2.amount)).normalize()),
            "DETAIL",
            "AccR_New",
            tx2.source_file,
        )
        assert updated_link.settlement_id == new_tx1_id
        assert updated_link.detail_id == new_tx2_id

    def test_internal_transfer_link_updated(self, session):
        acc = _insert_account(session, "AccT")
        tx_out = _insert_tx(session, account_label="AccT", date="2025-04-01", raw_description="BONIFICO OUT")
        tx_in = _insert_tx(session, account_label="AccT", date="2025-04-01", raw_description="BONIFICO IN")
        link = InternalTransferLink(out_id=tx_out.id, in_id=tx_in.id, confidence="high")
        session.add(link)
        session.flush()

        rename_account(session, acc.id, "AccT_Renamed")
        session.flush()
        session.expire_all()  # force reload from DB after raw SQL updates

        updated_link = session.query(InternalTransferLink).filter_by(id=link.id).one()
        new_out_id = _make_tx_id(
            tx_out.date,
            str(Decimal(str(tx_out.amount)).normalize()),
            "BONIFICO OUT",
            "AccT_Renamed",
            tx_out.source_file,
        )
        new_in_id = _make_tx_id(
            tx_in.date,
            str(Decimal(str(tx_in.amount)).normalize()),
            "BONIFICO IN",
            "AccT_Renamed",
            tx_in.source_file,
        )
        assert updated_link.out_id == new_out_id
        assert updated_link.in_id == new_in_id


# ── DocumentSchema cascade ───────────────────────────────────────────────────

class TestRenameAccountSchemas:
    """Verify document_schema account_label is updated on rename."""

    def test_schema_account_label_updated(self, session):
        acc = _insert_account(session, "SchemaAcc")
        schema = DocumentSchemaModel(
            source_identifier="cols:abc123",
            doc_type="conto_corrente",
            account_label="SchemaAcc",
        )
        session.add(schema)
        session.flush()

        rename_account(session, acc.id, "SchemaAcc_New")
        session.flush()

        refreshed = session.query(DocumentSchemaModel).filter_by(id=schema.id).one()
        assert refreshed.account_label == "SchemaAcc_New"


# ── Atomicity ────────────────────────────────────────────────────────────────

class TestRenameAccountAtomicity:
    """Verify that if the rename fails partway, nothing changes."""

    def test_collision_rolls_back_cleanly(self, session):
        """If the new name collides, ValueError is raised before any tx mutation."""
        _insert_account(session, "Existing")
        acc = _insert_account(session, "ToRename")
        tx = _insert_tx(session, account_label="ToRename")
        old_id = tx.id
        session.commit()  # persist so rollback has a savepoint

        with pytest.raises(ValueError):
            rename_account(session, acc.id, "Existing")

        # The ValueError is raised at the Account flush (duplicate name),
        # BEFORE any tx_id mutation happens — so rollback restores everything
        session.rollback()
        found = session.get(Transaction, old_id)
        assert found is not None
        assert found.account_label == "ToRename"


# ── updated_at behaviour ─────────────────────────────────────────────────────

class TestUpdatedAt:
    """Verify that updated_at is set automatically on ORM-level updates."""

    def test_updated_at_none_on_insert(self, session):
        tx = _insert_tx(session, account_label="TestAcc")
        assert tx.updated_at is None

    def test_updated_at_set_on_orm_update(self, session):
        tx = _insert_tx(session, account_label="TestAcc")
        assert tx.updated_at is None

        # Modify via ORM to trigger onupdate
        tx.description = "modified description"
        session.flush()

        # Refresh to pick up the onupdate value
        session.refresh(tx)
        assert tx.updated_at is not None
        assert isinstance(tx.updated_at, datetime)

    def test_updated_at_changes_on_subsequent_update(self, session):
        tx = _insert_tx(session, account_label="TestAcc")

        tx.category = "Alimentari"
        session.flush()
        session.refresh(tx)
        first_update = tx.updated_at
        assert first_update is not None

        # Small delay to ensure timestamps differ
        time.sleep(0.05)

        tx.category = "Trasporti"
        session.flush()
        session.refresh(tx)
        second_update = tx.updated_at
        assert second_update is not None
        assert second_update >= first_update

    def test_created_at_not_changed_on_update(self, session):
        tx = _insert_tx(session, account_label="TestAcc")
        # Compare without tz info (SQLite strips timezone on round-trip)
        original_created = tx.created_at.replace(tzinfo=None) if tx.created_at else None

        tx.description = "changed"
        session.flush()
        session.refresh(tx)

        refreshed_created = tx.created_at.replace(tzinfo=None) if tx.created_at else None
        assert refreshed_created == original_created
