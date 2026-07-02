# mailquill 計畫 4：Report HTML Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 由 SQLite 產生一份自包含、離線可看的 HTML dashboard，供核對：總額/筆數/期間、消費分類統計（第一層）、月度統計、完整明細表；並以 `mailquill report` 指令一鍵產出。

**Architecture:** 純本地、零外部資源。報表彙整用 SQL（`aggregate`），版面用 Python 字串組裝（內嵌 CSS、CSS 長條圖，不依賴任何 JS/CDN，確保離線與隱私）。金額在 SQLite 以 `CAST(amount AS REAL)` 彙總，空字串金額自動計為 0、仍出現在明細。

**Tech Stack:** Python 3.13、標準庫（`sqlite3`、`html`、`dataclasses`）、`pytest`。所有指令用 `.venv/bin/python`。

## Global Constraints

- 全本地處理；報表為單一自包含 HTML 檔，不引用任何外部資源（無 CDN/字型/JS 函式庫）。
- 純讀取 SQLite，不修改資料；SQLite 由計畫 1 的 `rebuild_sqlite` 從 CSV 重建。
- 金額彙總用 `CAST(amount AS REAL)`；空字串金額計為 0 但仍列入明細（不丟資料）。
- 商家等使用者資料輸出到 HTML 前一律 `html.escape`，避免版面破壞。
- 所有測試用 `.venv/bin/python -m pytest`。

---

## File Structure

```
mailquill/
  report.py    # ReportData + aggregate(db_path) + render_html(data) + generate_report(db_path, out)
  cli.py       # 追加 `report` subcommand（modify）
tests/
  test_report.py
```

---

### Task 1: 報表彙整 aggregate

**Files:**
- Create: `mailquill/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Produces:
  - `ReportData` dataclass：`total: float`, `count: int`,
    `by_l1: list[tuple[str, float]]`（第一層分類, 金額；金額由大到小），
    `by_month: list[tuple[str, float]]`（`YYYY-MM`, 金額；月份由小到大），
    `rows: list[dict]`（每筆含 `date`/`merchant`/`amount`/`l1`/`l2`/`bank`；日期新到舊）
  - `aggregate(db_path: str) -> ReportData` — 唯讀查詢 SQLite 的 `transactions` 表

- [ ] **Step 1: 寫失敗測試**

`tests/test_report.py`:
```python
from mailquill.schema import Transaction, make_txn_id
from mailquill.store import append_transactions, rebuild_sqlite
from mailquill.report import aggregate, ReportData


def _txn(date, merchant, amount, l1, l2="", bank="B"):
    tid = make_txn_id(bank, "1234", date, amount, merchant)
    return Transaction(
        txn_id=tid, date=date, post_date="", amount=amount, currency="TWD",
        merchant_raw=merchant, merchant_norm=merchant, category_l1=l1, category_l2=l2,
        bank=bank, account_last4="1234", source_type="pdf", source_msg_id="m1",
        raw_ref="", imported_at="2026-06-24T00:00:00",
    )


def _db(tmp_path, txns):
    csv_path = str(tmp_path / "t.csv")
    db_path = str(tmp_path / "t.db")
    append_transactions(csv_path, txns)
    rebuild_sqlite(csv_path, db_path)
    return db_path


def test_aggregate_totals_and_breakdowns(tmp_path):
    db = _db(tmp_path, [
        _txn("2026-05-10", "全聯", "300", "食", "生活採買"),
        _txn("2026-06-01", "全聯", "1200", "食", "生活採買"),
        _txn("2026-06-02", "台電", "800", "住", "水電瓦斯"),
    ])
    data = aggregate(db)
    assert isinstance(data, ReportData)
    assert data.count == 3
    assert abs(data.total - 2300.0) < 1e-9
    # 第一層：食 1500 > 住 800
    assert data.by_l1[0] == ("食", 1500.0)
    assert data.by_l1[1] == ("住", 800.0)
    # 月度：2026-05 在前、2026-06 在後
    assert data.by_month[0] == ("2026-05", 300.0)
    assert data.by_month[1] == ("2026-06", 2000.0)
    # 明細：日期新到舊，第一筆是 06-02
    assert data.rows[0]["date"] == "2026-06-02"
    assert data.rows[0]["merchant"] == "台電"


def test_aggregate_empty_amount_counts_as_zero_but_row_present(tmp_path):
    db = _db(tmp_path, [
        _txn("2026-06-01", "正常", "100", "食"),
        _txn("2026-06-02", "壞資料", "", "未分類"),
    ])
    data = aggregate(db)
    assert data.count == 2
    assert abs(data.total - 100.0) < 1e-9
    assert any(r["merchant"] == "壞資料" for r in data.rows)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_report.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'mailquill.report'`）

- [ ] **Step 3: 實作 aggregate**

`mailquill/report.py`（本任務先建立 ReportData 與 aggregate；render 在 Task 2 追加）:
```python
"""由 SQLite 彙整並產生自包含 HTML 報表。"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class ReportData:
    total: float
    count: int
    by_l1: list[tuple[str, float]]
    by_month: list[tuple[str, float]]
    rows: list[dict]


def aggregate(db_path: str) -> ReportData:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        total = cur.execute(
            "SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) FROM transactions"
        ).fetchone()[0]
        count = cur.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        by_l1 = cur.execute(
            "SELECT category_l1, COALESCE(SUM(CAST(amount AS REAL)), 0) AS s "
            "FROM transactions GROUP BY category_l1 ORDER BY s DESC"
        ).fetchall()
        by_month = cur.execute(
            "SELECT substr(date, 1, 7) AS ym, "
            "COALESCE(SUM(CAST(amount AS REAL)), 0) "
            "FROM transactions GROUP BY ym ORDER BY ym"
        ).fetchall()
        detail = cur.execute(
            "SELECT date, merchant_norm, amount, category_l1, category_l2, bank "
            "FROM transactions ORDER BY date DESC, rowid DESC"
        ).fetchall()
        rows = [
            {"date": r[0], "merchant": r[1], "amount": r[2],
             "l1": r[3], "l2": r[4], "bank": r[5]}
            for r in detail
        ]
        return ReportData(
            total=float(total),
            count=int(count),
            by_l1=[(a, float(b)) for a, b in by_l1],
            by_month=[(a, float(b)) for a, b in by_month],
            rows=rows,
        )
    finally:
        conn.close()
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_report.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add mailquill/report.py tests/test_report.py
git commit -m "feat: add report aggregation from SQLite"
```

---

### Task 2: HTML 渲染與 generate_report

**Files:**
- Modify: `mailquill/report.py`
- Test: `tests/test_report.py`（追加測試）

**Interfaces:**
- Consumes: `ReportData`, `aggregate`
- Produces:
  - `render_html(data: ReportData) -> str` — 產生自包含 HTML 字串（內嵌 CSS、CSS 長條圖；無外部資源）。含：總額/筆數/月份數摘要、第一層分類統計（含 CSS 長條）、月度統計（含 CSS 長條）、完整明細表。所有使用者字串經 `html.escape`。
  - `generate_report(db_path: str, out_path: str) -> str` — `aggregate` → `render_html` → 以 UTF-8 寫入 `out_path`，回傳 `out_path`

- [ ] **Step 1: 追加失敗測試**

在 `tests/test_report.py` 末尾追加：
```python
from mailquill.report import render_html, generate_report


def test_render_html_contains_key_sections(tmp_path):
    db = _db(tmp_path, [
        _txn("2026-06-01", "全聯", "1200", "食", "生活採買"),
        _txn("2026-06-02", "台電", "800", "住", "水電瓦斯"),
    ])
    html_str = render_html(aggregate(db))
    assert "<html" in html_str.lower()
    assert "mailquill" in html_str.lower()
    # 分類與金額有出現
    assert "食" in html_str
    assert "住" in html_str
    assert "全聯" in html_str
    assert "2026-06" in html_str
    # 自包含：不引用外部資源
    assert "http://" not in html_str
    assert "https://" not in html_str


def test_render_html_escapes_merchant(tmp_path):
    db = _db(tmp_path, [_txn("2026-06-01", "<script>x</script>", "100", "食")])
    html_str = render_html(aggregate(db))
    assert "<script>x</script>" not in html_str
    assert "&lt;script&gt;" in html_str


def test_generate_report_writes_file(tmp_path):
    db = _db(tmp_path, [_txn("2026-06-01", "全聯", "1200", "食")])
    out = str(tmp_path / "report.html")
    returned = generate_report(db, out)
    assert returned == out
    with open(out, encoding="utf-8") as f:
        content = f.read()
    assert "全聯" in content
    assert "<html" in content.lower()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_report.py -k "render or generate" -v`
Expected: FAIL（`cannot import name 'render_html'`）

- [ ] **Step 3: 追加 render_html 與 generate_report**

在 `mailquill/report.py` 檔頂的 import 追加 `import html`，並在檔案末尾追加：
```python
def _money(v: float) -> str:
    return f"{v:,.0f}"


def _bar_rows(items: list[tuple[str, float]]) -> str:
    if not items:
        return '<tr><td colspan="3">（無資料）</td></tr>'
    top = max((v for _, v in items), default=0) or 1
    out = []
    for label, value in items:
        pct = max(0.0, value / top * 100)
        out.append(
            "<tr>"
            f"<td class='label'>{html.escape(str(label))}</td>"
            f"<td class='barcell'><div class='bar' style='width:{pct:.1f}%'></div></td>"
            f"<td class='amt'>{_money(value)}</td>"
            "</tr>"
        )
    return "\n".join(out)


def _detail_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="6">（無資料）</td></tr>'
    out = []
    for r in rows:
        out.append(
            "<tr>"
            f"<td>{html.escape(str(r['date']))}</td>"
            f"<td>{html.escape(str(r['merchant']))}</td>"
            f"<td class='amt'>{html.escape(str(r['amount']))}</td>"
            f"<td>{html.escape(str(r['l1']))}</td>"
            f"<td>{html.escape(str(r['l2']))}</td>"
            f"<td>{html.escape(str(r['bank']))}</td>"
            "</tr>"
        )
    return "\n".join(out)


_STYLE = """
* { box-sizing: border-box; }
body { font-family: -apple-system, "PingFang TC", "Microsoft JhengHei", sans-serif;
       margin: 0; padding: 2rem; background: #f6f7f9; color: #1c1f23; }
h1 { font-size: 1.4rem; margin: 0 0 0.25rem; }
.sub { color: #6b7280; margin: 0 0 1.5rem; font-size: 0.9rem; }
.cards { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
.card { background: #fff; border-radius: 12px; padding: 1rem 1.25rem; min-width: 140px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
.card .k { color: #6b7280; font-size: 0.8rem; }
.card .v { font-size: 1.5rem; font-weight: 600; margin-top: 0.25rem; }
section { background: #fff; border-radius: 12px; padding: 1.25rem; margin-bottom: 1.5rem;
          box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
section h2 { font-size: 1.05rem; margin: 0 0 0.75rem; }
table { width: 100%; border-collapse: collapse; }
.tablewrap { overflow-x: auto; }
th, td { text-align: left; padding: 0.4rem 0.5rem; border-bottom: 1px solid #eef0f2;
         font-size: 0.9rem; white-space: nowrap; }
td.amt, th.amt { text-align: right; font-variant-numeric: tabular-nums; }
td.label { width: 8rem; }
td.barcell { width: 60%; }
.bar { background: linear-gradient(90deg,#4f8cff,#7aa7ff); height: 14px; border-radius: 7px; }
"""


def render_html(data: ReportData) -> str:
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mailquill 財務報表</title>
<style>{_STYLE}</style>
</head>
<body>
<h1>mailquill 財務報表</h1>
<p class="sub">本地產生 · 自包含離線檔</p>
<div class="cards">
  <div class="card"><div class="k">總額</div><div class="v">{_money(data.total)}</div></div>
  <div class="card"><div class="k">筆數</div><div class="v">{data.count}</div></div>
  <div class="card"><div class="k">月份數</div><div class="v">{len(data.by_month)}</div></div>
</div>
<section>
  <h2>消費分類統計（第一層）</h2>
  <table>{_bar_rows(data.by_l1)}</table>
</section>
<section>
  <h2>月度統計</h2>
  <table>{_bar_rows(data.by_month)}</table>
</section>
<section>
  <h2>明細（{data.count} 筆）</h2>
  <div class="tablewrap"><table>
    <tr><th>日期</th><th>商家</th><th class="amt">金額</th><th>第一層</th><th>第二層</th><th>來源</th></tr>
    {_detail_rows(data.rows)}
  </table></div>
</section>
</body>
</html>"""


def generate_report(db_path: str, out_path: str) -> str:
    html_str = render_html(aggregate(db_path))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_str)
    return out_path
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_report.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add mailquill/report.py tests/test_report.py
git commit -m "feat: render self-contained HTML report and write to file"
```

---

### Task 3: `report` CLI 指令

**Files:**
- Modify: `mailquill/cli.py`
- Test: `tests/test_cli_report.py`

**Interfaces:**
- Consumes: `config.load_config`、`report.generate_report`
- Produces:
  - CLI：`mailquill report [--config PATH] [--out PATH]`（`--out` 預設 `report.html`）—
    `load_config` → `generate_report(cfg.db_path, out)` → 印出輸出路徑 → 回 0
  - 以模組層名稱 import `generate_report`（讓測試可 monkeypatch `cli.generate_report`）

- [ ] **Step 1: 寫失敗測試**

`tests/test_cli_report.py`:
```python
import mailquill.cli as cli
from mailquill.config import Config


def test_main_report_invokes_generate_and_returns_zero(monkeypatch, tmp_path, capsys):
    cfg = Config(label="財務", db_path=str(tmp_path / "t.db"))
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)

    captured = {}

    def fake_generate(db_path, out):
        captured["db_path"] = db_path
        captured["out"] = out
        return out

    monkeypatch.setattr(cli, "generate_report", fake_generate)

    out_path = str(tmp_path / "r.html")
    rc = cli.main(["report", "--config", "x.yaml", "--out", out_path])
    assert rc == 0
    assert captured["db_path"] == cfg.db_path
    assert captured["out"] == out_path
    assert out_path in capsys.readouterr().out
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_cli_report.py -v`
Expected: FAIL（`SystemExit`/argparse：`report` 子指令尚未存在）

- [ ] **Step 3: 追加 `report` CLI 指令**

在 `mailquill/cli.py` 的 import 區追加：
```python
from mailquill.report import generate_report
```

在 `main` 內、`p_run` 區塊之後、`args = parser.parse_args(argv)` 之前追加：
```python
    p_report = sub.add_parser(
        "report", help="由 SQLite 產生自包含 HTML 報表"
    )
    p_report.add_argument("--config", default="config.yaml")
    p_report.add_argument("--out", default="report.html")
```

在 `main` 的分派區段（`run` 之後）追加：
```python
    if args.command == "report":
        cfg = load_config(args.config)
        out = generate_report(cfg.db_path, args.out)
        print(f"report: 已產生 {out}")
        return 0
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_cli_report.py -v`
Expected: PASS（1 passed）

- [ ] **Step 5: 跑全套測試**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS（計畫 1-3 的 65 + 本計畫新增，全綠）

- [ ] **Step 6: Commit**

```bash
git add mailquill/cli.py tests/test_cli_report.py
git commit -m "feat: add report CLI command"
```

---

## Self-Review

**Spec coverage（本計畫範圍）：**
- 由 SQLite 產生自包含 HTML dashboard（分類圓餅/長條、月度、金流、明細）→ Task 1（彙整）+ Task 2（渲染）✅
  （以 CSS 長條圖與明細表呈現，離線、零外部資源；圓餅以第一層分類長條替代以維持零依賴與可測。）
- `report` 一鍵產出 → Task 3 ✅
- 空金額不丟資料、仍列明細 → Task 1 測試涵蓋 ✅

**Placeholder scan：** 無 TBD/TODO；每個程式步驟含完整程式碼。

**Type consistency：** `ReportData`、`aggregate`、`render_html`、`generate_report` 跨 task 名稱與簽章一致；CLI 追加沿用既有 `main`/`argparse` 結構與模組層 import 慣例。
