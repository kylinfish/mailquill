import sqlite3

from mailquill.schema import Transaction, make_txn_id, FIELDS
from mailquill.store import read_transactions, append_transactions, rebuild_sqlite


def _txn(merchant, amount="100.00"):
    tid = make_txn_id("Cathay", "1234", "2026-06-01", amount, merchant)
    return Transaction(
        txn_id=tid, date="2026-06-01", post_date="", amount=amount,
        currency="TWD", merchant_raw=merchant, merchant_norm="",
        category_l1="未分類", category_l2="", bank="Cathay",
        account_last4="1234", source_type="email_body",
        source_msg_id="m1", raw_ref="", imported_at="2026-06-24T00:00:00",
    )


def test_read_missing_file_returns_empty(tmp_path):
    assert read_transactions(str(tmp_path / "none.csv")) == []


def test_append_then_read_roundtrip(tmp_path):
    path = str(tmp_path / "t.csv")
    res = append_transactions(path, [_txn("全聯"), _txn("家樂福")])
    assert (res.added, res.skipped) == (2, 0)
    rows = read_transactions(path)
    assert {r.merchant_raw for r in rows} == {"全聯", "家樂福"}


def test_append_dedups_against_existing(tmp_path):
    path = str(tmp_path / "t.csv")
    append_transactions(path, [_txn("全聯")])
    res = append_transactions(path, [_txn("全聯"), _txn("家樂福")])
    assert (res.added, res.skipped) == (1, 1)
    assert len(read_transactions(path)) == 2


def test_append_dedups_within_batch(tmp_path):
    path = str(tmp_path / "t.csv")
    res = append_transactions(path, [_txn("全聯"), _txn("全聯")])
    assert (res.added, res.skipped) == (1, 1)


def test_header_written_once(tmp_path):
    path = str(tmp_path / "t.csv")
    append_transactions(path, [_txn("全聯")])
    append_transactions(path, [_txn("家樂福")])
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert lines[0].startswith("txn_id,")
    assert sum(1 for ln in lines if ln.startswith("txn_id,")) == 1


def test_rebuild_sqlite_loads_all_rows(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    db_path = str(tmp_path / "t.db")
    append_transactions(csv_path, [_txn("全聯", "100.00"), _txn("家樂福", "250.50")])
    n = rebuild_sqlite(csv_path, db_path)
    assert n == 2
    conn = sqlite3.connect(db_path)
    try:
        total = conn.execute("SELECT SUM(amount) FROM transactions").fetchone()[0]
        assert abs(total - 350.50) < 1e-9
        cols = [r[1] for r in conn.execute("PRAGMA table_info(transactions)")]
        assert cols == FIELDS
    finally:
        conn.close()


def test_rebuild_sqlite_is_idempotent(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    db_path = str(tmp_path / "t.db")
    append_transactions(csv_path, [_txn("全聯", "100.00")])
    rebuild_sqlite(csv_path, db_path)
    n = rebuild_sqlite(csv_path, db_path)
    assert n == 1
    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        assert count == 1
    finally:
        conn.close()
