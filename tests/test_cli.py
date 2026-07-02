import csv
import os
import sqlite3
import textwrap

import pytest

from mailquill.schema import Transaction
from mailquill.store import append_transactions, read_transactions
from mailquill.cli import rebuild, main


def _rules_file(tmp_path):
    p = tmp_path / "categories.yaml"
    p.write_text(textwrap.dedent("""
        rules:
          - {keyword: "全聯", l1: "食", l2: "生活採買"}
    """), encoding="utf-8")
    return str(p)


def _txn(merchant, l1="未分類", l2=""):
    return Transaction(
        txn_id=merchant, date="2026-06-01", post_date="", amount="100.00",
        currency="TWD", merchant_raw=merchant, merchant_norm="",
        category_l1=l1, category_l2=l2, bank="Cathay", account_last4="1234",
        source_type="email_body", source_msg_id="m1", raw_ref="",
        imported_at="2026-06-24T00:00:00",
    )


def test_rebuild_recategorizes_and_writes_back(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    db_path = str(tmp_path / "t.db")
    append_transactions(csv_path, [_txn("全聯福利中心"), _txn("未知商家")])
    n = rebuild(csv_path, db_path, _rules_file(tmp_path))
    assert n == 2

    rows = {r.merchant_raw: r for r in read_transactions(csv_path)}
    assert (rows["全聯福利中心"].category_l1, rows["全聯福利中心"].category_l2) == ("食", "生活採買")
    assert rows["未知商家"].category_l1 == "未分類"

    conn = sqlite3.connect(db_path)
    try:
        got = conn.execute(
            "SELECT category_l1 FROM transactions WHERE merchant_raw='全聯福利中心'"
        ).fetchone()[0]
        assert got == "食"
    finally:
        conn.close()


def test_main_rebuild_subcommand(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    db_path = str(tmp_path / "t.db")
    append_transactions(csv_path, [_txn("全聯福利中心")])
    rc = main([
        "rebuild", "--csv", csv_path, "--db", db_path,
        "--categories", _rules_file(tmp_path),
    ])
    assert rc == 0
    assert read_transactions(csv_path)[0].category_l1 == "食"


def test_rebuild_atomic_rewrite_failure_leaves_csv_intact(tmp_path, monkeypatch):
    """
    Regression guard: if rebuild() fails during categorization OR during the
    CSV write, the original CSV must be left byte-identical and no .tmp files
    may remain in the directory.

    Two sub-cases are exercised:

    (a) Failure before the CSV is touched: patch apply_categories to raise.
    (b) Failure mid-write: patch csv.DictWriter.writerow to raise after the
        temp file has been opened but before os.replace is called.
    """
    csv_path = str(tmp_path / "t.csv")
    db_path = str(tmp_path / "t.db")
    append_transactions(csv_path, [_txn("全聯福利中心"), _txn("未知商家")])
    original_bytes = open(csv_path, "rb").read()

    def _no_tmp_files():
        return [f for f in os.listdir(str(tmp_path)) if f.endswith(".tmp")]

    # --- sub-case (a): failure before CSV is touched ---
    import mailquill.cli as cli_mod
    monkeypatch.setattr(cli_mod, "apply_categories", lambda t, rules: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        rebuild(csv_path, db_path, _rules_file(tmp_path))

    assert open(csv_path, "rb").read() == original_bytes, "CSV mutated despite pre-write failure"
    assert _no_tmp_files() == [], "Leftover .tmp after pre-write failure"

    # Restore for sub-case (b)
    monkeypatch.undo()

    # --- sub-case (b): failure during write (after temp file opened) ---
    original_writerow = csv.DictWriter.writerow

    call_count = [0]

    def _failing_writerow(self, row):
        call_count[0] += 1
        if call_count[0] >= 1:
            raise RuntimeError("write boom")
        return original_writerow(self, row)

    monkeypatch.setattr(csv.DictWriter, "writerow", _failing_writerow)

    with pytest.raises(RuntimeError, match="write boom"):
        rebuild(csv_path, db_path, _rules_file(tmp_path))

    assert open(csv_path, "rb").read() == original_bytes, "CSV mutated despite mid-write failure"
    assert _no_tmp_files() == [], "Leftover .tmp after mid-write failure"
