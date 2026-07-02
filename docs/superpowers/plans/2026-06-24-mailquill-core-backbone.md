# mailquill 計畫 1：資料骨幹 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 mailquill 的資料核心：統一交易 schema、CSV 真實來源（含去重）、由 CSV 可重建的 SQLite、兩層 rule-based 分類，以及 `rebuild` CLI 指令。

**Architecture:** 純本地 Python。`transactions.csv` 是唯一真實來源；分類規則在 `categories.yaml`；`rebuild` 讀 CSV → 重新套用分類 → 回寫 CSV 分類欄 → 重建 SQLite。本計畫不碰 Gmail 或 PDF（在後續計畫），可用合成交易完整離線測試。

**Tech Stack:** Python 3.13、標準庫（`csv`、`sqlite3`、`hashlib`、`dataclasses`、`argparse`）、`PyYAML`、`pytest`。

## Global Constraints

- 全本地處理，零外部/網路呼叫。
- 純 rule-based，不使用任何 LLM。
- `transactions.csv` 為唯一真實來源；SQLite 與報表皆由其可重建。
- 永不靜默丟資料：無法分類者標 `未分類`，不得丟棄。
- CSV 欄位順序固定，與 schema 一致。
- 金額在 CSV 與 dataclass 中以字串保存（避免浮點 roundtrip 失真）；僅在寫入 SQLite 時轉 `float`。
- 套件名 `mailquill`；CLI 進入點 `mailquill`。

---

## File Structure

```
mailquill/
  pyproject.toml                # 套件設定與依賴
  categories.yaml               # 起始分類規則（非機密，進 git）
  mailquill/
    __init__.py
    schema.py                   # Transaction dataclass、FIELDS、make_txn_id、row 轉換
    store.py                    # CSV 讀取/去重寫入；SQLite 由 CSV 重建
    categorizer.py              # 載入 categories.yaml；指派 l1/l2
    cli.py                      # argparse 進入點；本計畫實作 `rebuild`
  tests/
    __init__.py
    test_schema.py
    test_store.py
    test_categorizer.py
```

---

### Task 1: 專案骨架與交易 schema

**Files:**
- Create: `pyproject.toml`
- Create: `mailquill/__init__.py`
- Create: `mailquill/schema.py`
- Create: `tests/__init__.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces:
  - `mailquill.schema.FIELDS: list[str]` — CSV 欄位順序
  - `mailquill.schema.Transaction` — dataclass，欄位同 `FIELDS`，全部為 `str`
  - `make_txn_id(bank: str, account_last4: str, date: str, amount: str, merchant_raw: str) -> str` — 回傳 16 字元 hex
  - `Transaction.to_row(self) -> dict[str, str]`
  - `Transaction.from_row(row: dict[str, str]) -> Transaction`（classmethod）

- [ ] **Step 1: 寫失敗測試**

`tests/test_schema.py`:
```python
from mailquill.schema import FIELDS, Transaction, make_txn_id


def test_fields_order_is_fixed():
    assert FIELDS == [
        "txn_id", "date", "post_date", "amount", "currency",
        "merchant_raw", "merchant_norm", "category_l1", "category_l2",
        "bank", "account_last4", "source_type", "source_msg_id",
        "raw_ref", "imported_at",
    ]


def test_make_txn_id_is_deterministic_and_short():
    a = make_txn_id("Cathay", "1234", "2026-06-01", "1200.00", "全聯福利中心")
    b = make_txn_id("Cathay", "1234", "2026-06-01", "1200.00", "全聯福利中心")
    assert a == b
    assert len(a) == 16
    assert a.isalnum()


def test_make_txn_id_differs_on_amount():
    a = make_txn_id("Cathay", "1234", "2026-06-01", "1200.00", "全聯福利中心")
    b = make_txn_id("Cathay", "1234", "2026-06-01", "1300.00", "全聯福利中心")
    assert a != b


def test_to_row_and_from_row_roundtrip():
    txn = Transaction(
        txn_id="abc", date="2026-06-01", post_date="2026-06-02",
        amount="1200.00", currency="TWD", merchant_raw="全聯",
        merchant_norm="全聯福利中心", category_l1="食", category_l2="生活採買",
        bank="Cathay", account_last4="1234", source_type="email_body",
        source_msg_id="msg-1", raw_ref="raw/msg-1.html", imported_at="2026-06-24T10:00:00",
    )
    row = txn.to_row()
    assert list(row.keys()) == FIELDS
    assert Transaction.from_row(row) == txn
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_schema.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'mailquill'`）

- [ ] **Step 3: 建立 pyproject 與套件**

`pyproject.toml`:
```toml
[project]
name = "mailquill"
version = "0.1.0"
description = "Local Gmail finance archiving and reporting agent"
requires-python = ">=3.11"
dependencies = ["PyYAML>=6.0"]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
mailquill = "mailquill.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["mailquill*"]
```

`mailquill/__init__.py`:
```python
__version__ = "0.1.0"
```

`tests/__init__.py`:
```python
```

`mailquill/schema.py`:
```python
"""統一交易 schema 與 ID 雜湊。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, fields

FIELDS = [
    "txn_id", "date", "post_date", "amount", "currency",
    "merchant_raw", "merchant_norm", "category_l1", "category_l2",
    "bank", "account_last4", "source_type", "source_msg_id",
    "raw_ref", "imported_at",
]


def make_txn_id(bank: str, account_last4: str, date: str,
                amount: str, merchant_raw: str) -> str:
    """以 source_account+date+amount+merchant 計算去重雜湊。"""
    source_account = f"{bank}:{account_last4}"
    key = "|".join([source_account, date, amount, merchant_raw])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


@dataclass
class Transaction:
    txn_id: str
    date: str
    post_date: str
    amount: str
    currency: str
    merchant_raw: str
    merchant_norm: str
    category_l1: str
    category_l2: str
    bank: str
    account_last4: str
    source_type: str
    source_msg_id: str
    raw_ref: str
    imported_at: str

    def to_row(self) -> dict[str, str]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "Transaction":
        return cls(**{name: row[name] for name in FIELDS})
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_schema.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml mailquill/__init__.py mailquill/schema.py tests/__init__.py tests/test_schema.py
git commit -m "feat: add project scaffold and transaction schema"
```

---

### Task 2: CSV store（讀取與去重寫入）

**Files:**
- Create: `mailquill/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `mailquill.schema.Transaction`, `FIELDS`
- Produces:
  - `read_transactions(csv_path: str) -> list[Transaction]`（檔案不存在回 `[]`）
  - `append_transactions(csv_path: str, txns: list[Transaction]) -> AppendResult`
  - `AppendResult` dataclass：`added: int`, `skipped: int`（依 `txn_id` 去重，含批次內重複）
  - 寫入時若檔案不存在則先寫 header；header 順序為 `FIELDS`

- [ ] **Step 1: 寫失敗測試**

`tests/test_store.py`:
```python
import os

from mailquill.schema import Transaction, make_txn_id
from mailquill.store import read_transactions, append_transactions


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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_store.py -v`
Expected: FAIL（`ModuleNotFoundError` / `cannot import name`）

- [ ] **Step 3: 實作 store**

`mailquill/store.py`:
```python
"""CSV 真實來源讀寫（含去重）。SQLite 重建在 Task 3 追加。"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass

from mailquill.schema import FIELDS, Transaction


@dataclass
class AppendResult:
    added: int
    skipped: int


def read_transactions(csv_path: str) -> list[Transaction]:
    if not os.path.exists(csv_path):
        return []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [Transaction.from_row(row) for row in reader]


def append_transactions(csv_path: str, txns: list[Transaction]) -> AppendResult:
    existing_ids = {t.txn_id for t in read_transactions(csv_path)}
    file_exists = os.path.exists(csv_path)
    added = skipped = 0
    batch_ids: set[str] = set()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if not file_exists:
            writer.writeheader()
        for txn in txns:
            if txn.txn_id in existing_ids or txn.txn_id in batch_ids:
                skipped += 1
                continue
            writer.writerow(txn.to_row())
            batch_ids.add(txn.txn_id)
            added += 1
    return AppendResult(added=added, skipped=skipped)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_store.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add mailquill/store.py tests/test_store.py
git commit -m "feat: add CSV store with dedup append/read"
```

---

### Task 3: 由 CSV 重建 SQLite

**Files:**
- Modify: `mailquill/store.py`
- Test: `tests/test_store.py`（追加測試）

**Interfaces:**
- Consumes: `read_transactions`, `Transaction`, `FIELDS`
- Produces:
  - `rebuild_sqlite(csv_path: str, db_path: str) -> int` — drop 後重建 `transactions` 表，由 CSV 全量載入，回傳載入筆數。`amount` 欄存為 `REAL`，其餘 `TEXT`。每次呼叫結果一致（idempotent）。

- [ ] **Step 1: 追加失敗測試**

在 `tests/test_store.py` 末尾追加：
```python
import sqlite3

from mailquill.store import rebuild_sqlite


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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_store.py -k rebuild -v`
Expected: FAIL（`cannot import name 'rebuild_sqlite'`）

- [ ] **Step 3: 實作 rebuild_sqlite**

在 `mailquill/store.py` 追加 `import sqlite3`（檔頂）與下列函式：
```python
def rebuild_sqlite(csv_path: str, db_path: str) -> int:
    txns = read_transactions(csv_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TABLE IF EXISTS transactions")
        col_defs = ", ".join(
            f"{name} REAL" if name == "amount" else f"{name} TEXT"
            for name in FIELDS
        )
        conn.execute(f"CREATE TABLE transactions ({col_defs})")
        placeholders = ", ".join("?" for _ in FIELDS)
        rows = []
        for t in txns:
            row = t.to_row()
            values = [
                float(row[name]) if name == "amount" and row[name] != "" else row[name]
                for name in FIELDS
            ]
            rows.append(values)
        conn.executemany(
            f"INSERT INTO transactions ({', '.join(FIELDS)}) VALUES ({placeholders})",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    return len(txns)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_store.py -v`
Expected: PASS（全部 store 測試 7 passed）

- [ ] **Step 5: Commit**

```bash
git add mailquill/store.py tests/test_store.py
git commit -m "feat: rebuild SQLite from CSV (idempotent)"
```

---

### Task 4: 兩層 rule-based 分類器

**Files:**
- Create: `categories.yaml`
- Create: `mailquill/categorizer.py`
- Test: `tests/test_categorizer.py`

**Interfaces:**
- Consumes: `mailquill.schema.Transaction`
- Produces:
  - `load_categories(path: str) -> list[Rule]`
  - `Rule` dataclass：`keyword: str`, `l1: str`, `l2: str`
  - `categorize(merchant_raw: str, rules: list[Rule]) -> tuple[str, str]` — 回傳第一個 `keyword` 出現在 `merchant_raw` 內的 `(l1, l2)`；都不中回 `("未分類", "")`
  - `apply_categories(txn: Transaction, rules: list[Rule]) -> Transaction` — 回傳更新 `category_l1`/`category_l2` 後的新 `Transaction`（其餘欄不變）

`categories.yaml` 格式（關鍵字 → 兩層）：
```yaml
rules:
  - keyword: "全聯"
    l1: "食"
    l2: "生活採買"
  - keyword: "家樂福"
    l1: "食"
    l2: "生活採買"
  - keyword: "中華電信"
    l1: "住"
    l2: "通訊"
```

- [ ] **Step 1: 寫失敗測試**

`tests/test_categorizer.py`:
```python
import textwrap
from dataclasses import replace

from mailquill.schema import Transaction
from mailquill.categorizer import load_categories, categorize, apply_categories, Rule


def _write_rules(tmp_path):
    p = tmp_path / "categories.yaml"
    p.write_text(textwrap.dedent("""
        rules:
          - keyword: "全聯"
            l1: "食"
            l2: "生活採買"
          - keyword: "中華電信"
            l1: "住"
            l2: "通訊"
    """), encoding="utf-8")
    return str(p)


def _txn(merchant):
    return Transaction(
        txn_id="x", date="2026-06-01", post_date="", amount="100.00",
        currency="TWD", merchant_raw=merchant, merchant_norm="",
        category_l1="未分類", category_l2="", bank="Cathay",
        account_last4="1234", source_type="email_body",
        source_msg_id="m1", raw_ref="", imported_at="2026-06-24T00:00:00",
    )


def test_load_categories(tmp_path):
    rules = load_categories(_write_rules(tmp_path))
    assert rules[0] == Rule(keyword="全聯", l1="食", l2="生活採買")
    assert len(rules) == 2


def test_categorize_match_substring(tmp_path):
    rules = load_categories(_write_rules(tmp_path))
    assert categorize("全聯福利中心 信義店", rules) == ("食", "生活採買")


def test_categorize_no_match_returns_uncategorized(tmp_path):
    rules = load_categories(_write_rules(tmp_path))
    assert categorize("某不知名商家", rules) == ("未分類", "")


def test_apply_categories_returns_updated_copy(tmp_path):
    rules = load_categories(_write_rules(tmp_path))
    txn = _txn("中華電信")
    out = apply_categories(txn, rules)
    assert (out.category_l1, out.category_l2) == ("住", "通訊")
    assert out.merchant_raw == "中華電信"
    assert txn.category_l1 == "未分類"  # 原物件不被改動
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_categorizer.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'mailquill.categorizer'`）

- [ ] **Step 3: 建立規則檔與分類器**

`categories.yaml`（起始版，使用者日後自行擴充）:
```yaml
# 關鍵字 → (第一層, 第二層)；比對為「關鍵字出現在商家字串內」，由上而下取第一個命中。
rules:
  - {keyword: "全聯", l1: "食", l2: "生活採買"}
  - {keyword: "家樂福", l1: "食", l2: "生活採買"}
  - {keyword: "全家", l1: "食", l2: "便利商店"}
  - {keyword: "7-ELEVEN", l1: "食", l2: "便利商店"}
  - {keyword: "星巴克", l1: "食", l2: "餐飲"}
  - {keyword: "麥當勞", l1: "食", l2: "餐飲"}
  - {keyword: "Uber", l1: "行", l2: "計程車"}
  - {keyword: "計程車", l1: "行", l2: "計程車"}
  - {keyword: "高鐵", l1: "行", l2: "大眾運輸"}
  - {keyword: "悠遊卡", l1: "行", l2: "大眾運輸"}
  - {keyword: "中華電信", l1: "住", l2: "通訊"}
  - {keyword: "台電", l1: "住", l2: "水電瓦斯"}
  - {keyword: "自來水", l1: "住", l2: "水電瓦斯"}
  - {keyword: "Netflix", l1: "樂", l2: "串流訂閱"}
  - {keyword: "Spotify", l1: "樂", l2: "串流訂閱"}
  - {keyword: "誠品", l1: "育", l2: "書籍"}
  - {keyword: "藥局", l1: "醫療", l2: "藥品"}
  - {keyword: "UNIQLO", l1: "衣", l2: "服飾"}
```

`mailquill/categorizer.py`:
```python
"""兩層 rule-based 分類：關鍵字出現在商家字串內即命中。"""
from __future__ import annotations

from dataclasses import dataclass, replace

import yaml

from mailquill.schema import Transaction


@dataclass
class Rule:
    keyword: str
    l1: str
    l2: str


def load_categories(path: str) -> list[Rule]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return [
        Rule(keyword=r["keyword"], l1=r["l1"], l2=r["l2"])
        for r in data.get("rules", [])
    ]


def categorize(merchant_raw: str, rules: list[Rule]) -> tuple[str, str]:
    for rule in rules:
        if rule.keyword in merchant_raw:
            return (rule.l1, rule.l2)
    return ("未分類", "")


def apply_categories(txn: Transaction, rules: list[Rule]) -> Transaction:
    l1, l2 = categorize(txn.merchant_raw, rules)
    return replace(txn, category_l1=l1, category_l2=l2)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_categorizer.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add categories.yaml mailquill/categorizer.py tests/test_categorizer.py
git commit -m "feat: add two-layer rule-based categorizer"
```

---

### Task 5: `rebuild` CLI（重新分類 + 回寫 CSV + 重建 SQLite）

**Files:**
- Create: `mailquill/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `read_transactions`, `append_transactions`, `rebuild_sqlite`（store）；`load_categories`, `apply_categories`（categorizer）；`Transaction`, `FIELDS`（schema）
- Produces:
  - `rebuild(csv_path: str, db_path: str, categories_path: str) -> int` — 讀 CSV → 對每筆重新 `apply_categories` → 原子回寫 CSV（分類欄更新）→ `rebuild_sqlite`，回傳筆數
  - `main(argv: list[str] | None = None) -> int` — argparse；支援 `mailquill rebuild [--csv PATH] [--db PATH] [--categories PATH]`，預設 `transactions.csv` / `mailquill.db` / `categories.yaml`

- [ ] **Step 1: 寫失敗測試**

`tests/test_cli.py`:
```python
import sqlite3
import textwrap

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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_cli.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'mailquill.cli'`）

- [ ] **Step 3: 實作 CLI**

`mailquill/cli.py`:
```python
"""mailquill CLI。本計畫實作 `rebuild`；`bootstrap`/`run`/`report` 由後續計畫追加。"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile

from mailquill.categorizer import apply_categories, load_categories
from mailquill.schema import FIELDS
from mailquill.store import read_transactions, rebuild_sqlite


def _write_csv_atomic(csv_path: str, txns) -> None:
    dir_ = os.path.dirname(os.path.abspath(csv_path))
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            for t in txns:
                writer.writerow(t.to_row())
        os.replace(tmp, csv_path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def rebuild(csv_path: str, db_path: str, categories_path: str) -> int:
    rules = load_categories(categories_path)
    txns = [apply_categories(t, rules) for t in read_transactions(csv_path)]
    _write_csv_atomic(csv_path, txns)
    rebuild_sqlite(csv_path, db_path)
    return len(txns)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mailquill")
    sub = parser.add_subparsers(dest="command", required=True)

    p_rebuild = sub.add_parser(
        "rebuild", help="重新套用分類並由 CSV 重建 SQLite"
    )
    p_rebuild.add_argument("--csv", default="transactions.csv")
    p_rebuild.add_argument("--db", default="mailquill.db")
    p_rebuild.add_argument("--categories", default="categories.yaml")

    args = parser.parse_args(argv)
    if args.command == "rebuild":
        n = rebuild(args.csv, args.db, args.categories)
        print(f"rebuilt {n} transactions -> {args.db}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_cli.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 跑全套測試**

Run: `python3 -m pytest -v`
Expected: PASS（全部 17 passed）

- [ ] **Step 6: Commit**

```bash
git add mailquill/cli.py tests/test_cli.py
git commit -m "feat: add rebuild CLI command (recategorize + rebuild SQLite)"
```

---

## Self-Review

**Spec coverage（本計畫範圍）：**
- 統一交易 schema → Task 1 ✅
- CSV 真實來源 + 去重 → Task 2 ✅
- SQLite 由 CSV 可重建（idempotent）→ Task 3 ✅
- 兩層 rule-based 分類 + 可編輯 `categories.yaml` + 未分類處理 → Task 4 ✅
- `rebuild`（改規則後全部重新分類，不需重抓信）→ Task 5 ✅
- 後續計畫覆蓋：gmail_client / bootstrap / pdf_unlocker / parsers / normalizer / report（計畫 2、3）

**Placeholder scan：** 無 TBD/TODO；每個程式步驟均含完整可執行程式碼。

**Type consistency：** `Transaction`、`FIELDS`、`AppendResult`、`Rule`、`make_txn_id`、`read_transactions`、`append_transactions`、`rebuild_sqlite`、`load_categories`、`categorize`、`apply_categories`、`rebuild`、`main` 名稱與簽章跨 task 一致。
