# mailquill 計畫 3：Transform & Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把收進來的信件與解密後的 PDF，經由各家銀行的 rule-based parser 轉成原始交易、正規化成統一 schema、套用兩層分類，寫進 CSV 並重建 SQLite；以 `mailquill run` 串起整條 pipeline，並對「無對應 parser / 解密失敗 / 解析警告」提供可見清單（永不靜默丟資料）。

**Architecture:** 純本地、純 rule-based。parser 以 registry 註冊、各家一個模組、靠統一 schema 與其他元件隔離。本計畫附一個 ASCII 合成格式的「範例 parser」證明整條鏈路並作為新增銀行的模板；真實銀行 parser 在使用者提供去識別化樣本後再逐一加入（n2n 驗收時用 `bootstrap`+`run` 找出缺哪幾家）。pipeline 注入 Gmail service（與計畫 2 一致），測試用 fake service + reportlab 產生的測試 PDF 做整合測試。

**Tech Stack:** Python 3.13、`pdfplumber`（PDF 抽文字）、`pikepdf`（解密，計畫 2 已有）、`PyYAML`、`pytest`、`reportlab`（**dev only**，測試產生文字 PDF）。所有指令用 `.venv/bin/python`。

## Global Constraints

- 全本地處理；純 rule-based，不使用任何 LLM。
- `transactions.csv` 為唯一真實來源；SQLite 由其重建。
- 金額在 dataclass/CSV 中為字串；正規化只清掉千分位與貨幣符號，保留數字、負號、小數點。
- 永不靜默丟資料：無 parser → 記 `needs_parser`；PDF 解不開 → 記 `unlock_failures`；金額/日期清洗不出 → `category_l1` 仍照常、該筆記 `parse_warnings`，原始字串保留。
- 去重靠計畫 1 的 `make_txn_id`；重跑同信不重複入帳。
- parser 之間以統一 schema 隔離：新增銀行只新增 `parsers/<bank>.py` + 樣本測試，不改其他元件。
- 所有測試用 `.venv/bin/python -m pytest`。

---

## File Structure

```
mailquill/
  raw_txn.py          # RawTxn：parser 的輸出（正規化前）
  normalizer.py       # normalize_amount/normalize_date + normalize() -> Transaction
  pdf_text.py         # extract_pdf_text(pdf_bytes) -> str（pdfplumber）
  parsers/
    __init__.py       # registry：register / get_parser / all_parsers，並註冊範例 parser
    base.py           # Parser 基底類別（matches / parse）
    example_bank.py    # ASCII 合成格式範例 parser（模板）
  pipeline.py         # RunResult + run_pipeline（串接整條流程）
  cli.py              # 追加 `run` subcommand（modify）
tests/
  test_normalizer.py
  test_pdf_text.py
  test_parsers.py
  test_pipeline.py
```

---

### Task 1: RawTxn 與正規化

**Files:**
- Create: `mailquill/raw_txn.py`
- Create: `mailquill/normalizer.py`
- Test: `tests/test_normalizer.py`

**Interfaces:**
- Consumes: `mailquill.schema.Transaction`, `make_txn_id`
- Produces:
  - `RawTxn` dataclass：`bank: str`, `date: str`, `amount: str`, `merchant_raw: str`,
    `currency: str = "TWD"`, `post_date: str = ""`, `account_last4: str = ""`
  - `normalize_amount(raw: str) -> str` — 取第一個數字串、去千分位逗號；保留負號與小數點；無數字回 `""`
  - `normalize_date(raw: str) -> str` — `strip` 後把 `/` 與 `.` 換成 `-`
  - `normalize(raw: RawTxn, msg_id: str, source_type: str, raw_ref: str, imported_at: str) -> Transaction`
    — 清洗金額/日期、`merchant_norm = merchant_raw.strip()`、`category_l1="未分類"`/`category_l2=""`、
    `txn_id = make_txn_id(bank, account_last4, date, amount, merchant_raw)`（用清洗後的 date/amount）

- [ ] **Step 1: 寫失敗測試**

`tests/test_normalizer.py`:
```python
from mailquill.raw_txn import RawTxn
from mailquill.normalizer import normalize_amount, normalize_date, normalize
from mailquill.schema import make_txn_id


def test_normalize_amount_strips_separators_and_symbols():
    assert normalize_amount("1,200") == "1200"
    assert normalize_amount("$1,200.50") == "1200.50"
    assert normalize_amount("NT$ 1,200") == "1200"
    assert normalize_amount("1200元") == "1200"
    assert normalize_amount("-50.00") == "-50.00"
    assert normalize_amount("") == ""
    assert normalize_amount("無金額") == ""


def test_normalize_date_unifies_separators():
    assert normalize_date(" 2026/06/01 ") == "2026-06-01"
    assert normalize_date("2026.06.01") == "2026-06-01"
    assert normalize_date("2026-06-01") == "2026-06-01"


def test_normalize_builds_transaction():
    raw = RawTxn(bank="ExampleBank", date="2026/06/01", amount="1,200",
                 merchant_raw=" PXMART ", account_last4="1234")
    txn = normalize(raw, msg_id="m1", source_type="pdf",
                    raw_ref="raw/m1", imported_at="2026-06-24T10:00:00")
    assert txn.date == "2026-06-01"
    assert txn.amount == "1200"
    assert txn.merchant_raw == " PXMART "
    assert txn.merchant_norm == "PXMART"
    assert txn.category_l1 == "未分類"
    assert txn.category_l2 == ""
    assert txn.bank == "ExampleBank"
    assert txn.account_last4 == "1234"
    assert txn.currency == "TWD"
    assert txn.source_type == "pdf"
    assert txn.source_msg_id == "m1"
    assert txn.raw_ref == "raw/m1"
    assert txn.imported_at == "2026-06-24T10:00:00"
    assert txn.txn_id == make_txn_id("ExampleBank", "1234", "2026-06-01", "1200", " PXMART ")
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_normalizer.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'mailquill.raw_txn'`）

- [ ] **Step 3: 實作 raw_txn 與 normalizer**

`mailquill/raw_txn.py`:
```python
"""parser 的輸出：正規化前的原始交易。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RawTxn:
    bank: str
    date: str
    amount: str
    merchant_raw: str
    currency: str = "TWD"
    post_date: str = ""
    account_last4: str = ""
```

`mailquill/normalizer.py`:
```python
"""把 RawTxn 清洗、組成統一 schema 的 Transaction。"""
from __future__ import annotations

import re

from mailquill.raw_txn import RawTxn
from mailquill.schema import Transaction, make_txn_id

_AMOUNT_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def normalize_amount(raw: str) -> str:
    m = _AMOUNT_RE.search(raw or "")
    if not m:
        return ""
    return m.group(0).replace(",", "")


def normalize_date(raw: str) -> str:
    return (raw or "").strip().replace("/", "-").replace(".", "-")


def normalize(raw: RawTxn, msg_id: str, source_type: str,
              raw_ref: str, imported_at: str) -> Transaction:
    date = normalize_date(raw.date)
    post_date = normalize_date(raw.post_date) if raw.post_date else ""
    amount = normalize_amount(raw.amount)
    return Transaction(
        txn_id=make_txn_id(raw.bank, raw.account_last4, date, amount, raw.merchant_raw),
        date=date,
        post_date=post_date,
        amount=amount,
        currency=raw.currency,
        merchant_raw=raw.merchant_raw,
        merchant_norm=raw.merchant_raw.strip(),
        category_l1="未分類",
        category_l2="",
        bank=raw.bank,
        account_last4=raw.account_last4,
        source_type=source_type,
        source_msg_id=msg_id,
        raw_ref=raw_ref,
        imported_at=imported_at,
    )
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_normalizer.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add mailquill/raw_txn.py mailquill/normalizer.py tests/test_normalizer.py
git commit -m "feat: add RawTxn model and normalizer"
```

---

### Task 2: PDF 文字抽取

**Files:**
- Create: `mailquill/pdf_text.py`
- Modify: `pyproject.toml`（dev 依賴加 `reportlab`）
- Test: `tests/test_pdf_text.py`

**Interfaces:**
- Produces:
  - `extract_pdf_text(pdf_bytes: bytes) -> str` — 用 `pdfplumber` 抽出所有頁文字，以換行串接；無文字頁回空字串

- [ ] **Step 1: 寫失敗測試**

`tests/test_pdf_text.py`:
```python
import io

from reportlab.pdfgen import canvas

from mailquill.pdf_text import extract_pdf_text


def _text_pdf(lines: list[str]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 750
    for line in lines:
        c.drawString(72, y, line)
        y -= 20
    c.save()
    return buf.getvalue()


def test_extract_pdf_text_reads_lines():
    pdf = _text_pdf(["2026-06-01 PXMART 1,200", "2026-06-02 UBER 250"])
    text = extract_pdf_text(pdf)
    assert "PXMART" in text
    assert "1,200" in text
    assert "UBER" in text


def test_extract_pdf_text_empty_pdf_returns_empty_string():
    import pikepdf
    pdf = pikepdf.new()
    pdf.add_blank_page()
    buf = io.BytesIO()
    pdf.save(buf)
    assert extract_pdf_text(buf.getvalue()) == ""
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_pdf_text.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'mailquill.pdf_text'`）

- [ ] **Step 3: 加 dev 依賴並實作**

在 `pyproject.toml` 的 `[project.optional-dependencies]` 的 `dev` 陣列加入 `reportlab`，例如：
```toml
[project.optional-dependencies]
dev = ["pytest>=8.0", "reportlab>=4.0"]
```
（reportlab 已安裝於 `.venv`；此步僅登記為 dev 依賴。）

`mailquill/pdf_text.py`:
```python
"""用 pdfplumber 從 PDF bytes 抽出文字。"""
from __future__ import annotations

import io

import pdfplumber


def extract_pdf_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_pdf_text.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add mailquill/pdf_text.py pyproject.toml tests/test_pdf_text.py
git commit -m "feat: add PDF text extraction via pdfplumber"
```

---

### Task 3: Parser 基底、registry 與範例 parser

**Files:**
- Create: `mailquill/parsers/__init__.py`
- Create: `mailquill/parsers/base.py`
- Create: `mailquill/parsers/example_bank.py`
- Test: `tests/test_parsers.py`

**Interfaces:**
- Consumes: `mailquill.gmail_message.EmailMessage`、`mailquill.raw_txn.RawTxn`
- Produces:
  - `base.Parser` — 基底類別：類別屬性 `bank: str = ""`；`matches(self, msg: EmailMessage) -> bool`、
    `parse(self, msg: EmailMessage, pdf_texts: list[str]) -> list[RawTxn]`（基底拋 `NotImplementedError`）
  - `parsers.register(parser: Parser) -> None`
  - `parsers.get_parser(msg: EmailMessage) -> Parser | None` — 回傳第一個 `matches` 為真的 parser，否則 `None`
  - `parsers.all_parsers() -> list[Parser]`
  - `example_bank.ExampleBankParser`（`bank="ExampleBank"`）：`matches` 比對寄件者含 `@example-bank.test`；
    `parse` 從 `body_text` 與所有 `pdf_texts` 逐行比對 `YYYY-MM-DD 商家 金額`，產生 `RawTxn`
  - `parsers/__init__.py` 於載入時 `register(ExampleBankParser())`

- [ ] **Step 1: 寫失敗測試**

`tests/test_parsers.py`:
```python
from mailquill.gmail_message import EmailMessage
from mailquill.raw_txn import RawTxn
from mailquill.parsers import get_parser, all_parsers, register
from mailquill.parsers.base import Parser
from mailquill.parsers.example_bank import ExampleBankParser


def _msg(sender, body=""):
    return EmailMessage(msg_id="m1", sender=sender, subject="帳單", date="",
                        body_text=body, attachments=[])


def test_example_parser_registered():
    assert any(isinstance(p, ExampleBankParser) for p in all_parsers())


def test_get_parser_matches_example_bank():
    msg = _msg("帳單 <ebill@example-bank.test>")
    p = get_parser(msg)
    assert isinstance(p, ExampleBankParser)


def test_get_parser_returns_none_for_unknown():
    assert get_parser(_msg("noreply <x@unknown.test>")) is None


def test_example_parser_parses_body_and_pdf_lines():
    msg = _msg("ebill@example-bank.test", body="2026-06-01 PXMART 1,200")
    txns = get_parser(msg).parse(msg, ["2026-06-02 UBER 250"])
    assert [(t.date, t.merchant_raw, t.amount) for t in txns] == [
        ("2026-06-01", "PXMART", "1,200"),
        ("2026-06-02", "UBER", "250"),
    ]
    assert all(t.bank == "ExampleBank" for t in txns)


def test_base_parser_raises_not_implemented():
    msg = _msg("a@b.test")
    base = Parser()
    try:
        base.matches(msg)
        assert False, "should raise"
    except NotImplementedError:
        pass
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_parsers.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'mailquill.parsers'`）

- [ ] **Step 3: 實作 base、registry、範例 parser**

`mailquill/parsers/base.py`:
```python
"""Parser 基底類別。各家銀行繼承並實作 matches / parse。"""
from __future__ import annotations

from mailquill.gmail_message import EmailMessage
from mailquill.raw_txn import RawTxn


class Parser:
    bank: str = ""

    def matches(self, msg: EmailMessage) -> bool:
        raise NotImplementedError

    def parse(self, msg: EmailMessage, pdf_texts: list[str]) -> list[RawTxn]:
        raise NotImplementedError
```

`mailquill/parsers/example_bank.py`:
```python
"""範例 parser（模板）。比對測試用網域，不會命中真實銀行信件。

新增一家銀行 = 複製本檔，改 bank、matches 的寄件者比對、parse 的版型規則，
並在 parsers/__init__.py 註冊一個實例。
"""
from __future__ import annotations

import re

from mailquill.gmail_message import EmailMessage
from mailquill.raw_txn import RawTxn
from mailquill.parsers.base import Parser

_LINE_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s+(.+?)\s+([\d,]+(?:\.\d+)?)\s*$")


class ExampleBankParser(Parser):
    bank = "ExampleBank"

    def matches(self, msg: EmailMessage) -> bool:
        return "@example-bank.test" in msg.sender

    def parse(self, msg: EmailMessage, pdf_texts: list[str]) -> list[RawTxn]:
        txns: list[RawTxn] = []
        blocks = [msg.body_text] + list(pdf_texts)
        for block in blocks:
            for line in block.splitlines():
                m = _LINE_RE.match(line)
                if m:
                    txns.append(RawTxn(
                        bank=self.bank,
                        date=m.group(1),
                        merchant_raw=m.group(2),
                        amount=m.group(3),
                    ))
        return txns
```

`mailquill/parsers/__init__.py`:
```python
"""Parser registry。各家 parser 在此註冊。"""
from __future__ import annotations

from mailquill.gmail_message import EmailMessage
from mailquill.parsers.base import Parser

_PARSERS: list[Parser] = []


def register(parser: Parser) -> None:
    _PARSERS.append(parser)


def get_parser(msg: EmailMessage) -> Parser | None:
    for p in _PARSERS:
        if p.matches(msg):
            return p
    return None


def all_parsers() -> list[Parser]:
    return list(_PARSERS)


# 註冊內建 parser（真實銀行 parser 在取得樣本後逐一加入）
from mailquill.parsers.example_bank import ExampleBankParser  # noqa: E402

register(ExampleBankParser())
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_parsers.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add mailquill/parsers/ tests/test_parsers.py
git commit -m "feat: add parser base, registry, and example reference parser"
```

---

### Task 4: Pipeline 串接

**Files:**
- Create: `mailquill/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `config.Config`/`load_passwords`、`rules.load_rules`/`matches`、`gmail_client.list_label_messages`/`extract_message`、`pdf_unlocker.unlock_pdf`、`pdf_text.extract_pdf_text`、`parsers.get_parser`、`normalizer.normalize`、`categorizer.load_categories`/`apply_categories`、`store.append_transactions`/`rebuild_sqlite`
- Produces:
  - `RunResult` dataclass：`fetched: int`, `matched: int`, `added: int`, `skipped: int`,
    `needs_parser: list[str]`, `unlock_failures: list[str]`, `parse_warnings: list[str]`
  - `run_pipeline(service, cfg: Config, imported_at: str) -> RunResult`

  流程：列出 `cfg.label` 訊息 → 每封 `extract_message` → `matches(rules,…)` 過濾 →
  `get_parser`（無→記 `needs_parser`，跳過）→ 對每個 PDF 附件 `unlock_pdf`（失敗→記 `unlock_failures`，跳過該附件）→
  `extract_pdf_text` → `parser.parse` → 逐筆 `normalize`（`source_type` = 有 PDF 文字則 `"pdf"` 否則 `"email_body"`；
  `amount==""` 記 `parse_warnings`）→ `apply_categories` → 收集；最後 `append_transactions` + `rebuild_sqlite`。
  以 `import` 取得各依賴函式為**模組層名稱**（讓測試可 monkeypatch `get_parser`、`list_label_messages`、`extract_message`）。

說明：`service` 為注入物件（測試用 fake service）。`build_service` 不在此呼叫（在 CLI）。`raw_ref` 設為 `msg.msg_id`（本計畫不另存原檔；原檔歸檔可後續加強，`source_msg_id` 已可回溯 Gmail）。

- [ ] **Step 1: 寫失敗測試**

`tests/test_pipeline.py`:
```python
import base64
import io

import pikepdf
from reportlab.pdfgen import canvas

from mailquill.config import Config
from mailquill.rules import Rules
from mailquill.gmail_message import EmailMessage, Attachment
from mailquill.store import read_transactions
import mailquill.pipeline as pipeline


def _encrypted_pdf(lines, password):
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 750
    for ln in lines:
        c.drawString(72, y, ln)
        y -= 20
    c.save()
    buf.seek(0)
    src = pikepdf.open(buf)
    enc = io.BytesIO()
    src.save(enc, encryption=pikepdf.Encryption(owner=password, user=password))
    return enc.getvalue()


def _cfg(tmp_path):
    # 寫一份只含一條分類規則的 categories.yaml
    cats = tmp_path / "categories.yaml"
    cats.write_text('rules:\n  - {keyword: "PXMART", l1: "食", l2: "生活採買"}\n',
                    encoding="utf-8")
    pwd = tmp_path / "passwords.txt"
    pwd.write_text("PW1\n", encoding="utf-8")
    return Config(
        label="財務",
        csv_path=str(tmp_path / "t.csv"),
        db_path=str(tmp_path / "t.db"),
        categories_path=str(cats),
        rules_path=str(tmp_path / "rules.yaml"),
        passwords_path=str(pwd),
    )


def _setup_common(monkeypatch, tmp_path, messages, parser_for):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(pipeline, "load_rules",
                        lambda path: Rules(senders=["@example-bank.test", "@unknown.test"],
                                           subject_keywords=[]))
    monkeypatch.setattr(pipeline, "list_label_messages",
                        lambda service, label: list(messages.keys()))
    monkeypatch.setattr(pipeline, "extract_message",
                        lambda service, mid: messages[mid])
    monkeypatch.setattr(pipeline, "get_parser", parser_for)
    return cfg


def test_run_pipeline_parses_encrypted_pdf_and_writes_csv(monkeypatch, tmp_path):
    pdf = _encrypted_pdf(["2026-06-01 PXMART 1,200"], "PW1")
    msg = EmailMessage(
        msg_id="m1", sender="ebill@example-bank.test", subject="帳單", date="",
        body_text="", attachments=[Attachment("stmt.pdf", "application/pdf", pdf)],
    )
    from mailquill.parsers.example_bank import ExampleBankParser
    cfg = _setup_common(monkeypatch, tmp_path, {"m1": msg},
                        parser_for=lambda m: ExampleBankParser())

    result = pipeline.run_pipeline(service=object(), cfg=cfg,
                                   imported_at="2026-06-24T10:00:00")
    assert result.fetched == 1
    assert result.matched == 1
    assert result.added == 1
    assert result.needs_parser == []
    assert result.unlock_failures == []

    rows = read_transactions(cfg.csv_path)
    assert len(rows) == 1
    assert rows[0].merchant_norm == "PXMART"
    assert rows[0].amount == "1200"
    assert rows[0].category_l1 == "食"
    assert rows[0].source_type == "pdf"


def test_run_pipeline_records_needs_parser(monkeypatch, tmp_path):
    msg = EmailMessage(msg_id="m1", sender="x@unknown.test", subject="帳單",
                       date="", body_text="", attachments=[])
    cfg = _setup_common(monkeypatch, tmp_path, {"m1": msg}, parser_for=lambda m: None)
    result = pipeline.run_pipeline(service=object(), cfg=cfg,
                                   imported_at="2026-06-24T10:00:00")
    assert result.matched == 1
    assert result.added == 0
    assert len(result.needs_parser) == 1
    assert "unknown.test" in result.needs_parser[0]


def test_run_pipeline_records_unlock_failure(monkeypatch, tmp_path):
    pdf = _encrypted_pdf(["2026-06-01 PXMART 1,200"], "RIGHT")  # 清單裡只有 PW1
    msg = EmailMessage(
        msg_id="m1", sender="ebill@example-bank.test", subject="帳單", date="",
        body_text="", attachments=[Attachment("stmt.pdf", "application/pdf", pdf)],
    )
    from mailquill.parsers.example_bank import ExampleBankParser
    cfg = _setup_common(monkeypatch, tmp_path, {"m1": msg},
                        parser_for=lambda m: ExampleBankParser())
    result = pipeline.run_pipeline(service=object(), cfg=cfg,
                                   imported_at="2026-06-24T10:00:00")
    assert len(result.unlock_failures) == 1
    assert result.added == 0  # PDF 解不開、body 也無交易
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'mailquill.pipeline'`）

- [ ] **Step 3: 實作 pipeline**

`mailquill/pipeline.py`:
```python
"""串接：抓信 → 解密 PDF → parser → 正規化 → 分類 → CSV → SQLite。"""
from __future__ import annotations

from dataclasses import dataclass, field

from mailquill.config import Config, load_passwords
from mailquill.rules import load_rules, matches
from mailquill.gmail_client import list_label_messages, extract_message
from mailquill.pdf_unlocker import unlock_pdf
from mailquill.pdf_text import extract_pdf_text
from mailquill.parsers import get_parser
from mailquill.normalizer import normalize
from mailquill.categorizer import load_categories, apply_categories
from mailquill.store import append_transactions, rebuild_sqlite


@dataclass
class RunResult:
    fetched: int = 0
    matched: int = 0
    added: int = 0
    skipped: int = 0
    needs_parser: list[str] = field(default_factory=list)
    unlock_failures: list[str] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)


def _is_pdf(att) -> bool:
    return att.mime_type == "application/pdf" or att.filename.lower().endswith(".pdf")


def run_pipeline(service, cfg: Config, imported_at: str) -> RunResult:
    result = RunResult()
    rules = load_rules(cfg.rules_path)
    passwords = load_passwords(cfg.passwords_path)
    category_rules = load_categories(cfg.categories_path)

    ids = list_label_messages(service, cfg.label)
    result.fetched = len(ids)

    collected = []
    for mid in ids:
        msg = extract_message(service, mid)
        if not matches(rules, msg.sender, msg.subject):
            continue
        result.matched += 1

        parser = get_parser(msg)
        if parser is None:
            result.needs_parser.append(f"{msg.sender} | {msg.subject}")
            continue

        pdf_texts = []
        for att in msg.attachments:
            if not _is_pdf(att):
                continue
            res = unlock_pdf(att.data, passwords)
            if not res.ok:
                result.unlock_failures.append(f"{msg.msg_id}:{att.filename}")
                continue
            pdf_texts.append(extract_pdf_text(res.data))

        source_type = "pdf" if pdf_texts else "email_body"
        for raw in parser.parse(msg, pdf_texts):
            txn = normalize(raw, msg.msg_id, source_type, msg.msg_id, imported_at)
            if txn.amount == "":
                result.parse_warnings.append(f"{msg.msg_id}:{raw.merchant_raw}")
            collected.append(apply_categories(txn, category_rules))

    append_res = append_transactions(cfg.csv_path, collected)
    result.added = append_res.added
    result.skipped = append_res.skipped
    rebuild_sqlite(cfg.csv_path, cfg.db_path)
    return result
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add mailquill/pipeline.py tests/test_pipeline.py
git commit -m "feat: add run pipeline orchestration"
```

---

### Task 5: `run` CLI 指令

**Files:**
- Modify: `mailquill/cli.py`
- Test: `tests/test_cli_run.py`

**Interfaces:**
- Consumes: `config.load_config`、`gmail_client.build_service`、`pipeline.run_pipeline`
- Produces:
  - CLI：`mailquill run [--config PATH]` — `load_config` → `build_service` → `run_pipeline`（`imported_at` 用當下時間）
    → 印出摘要（fetched/matched/added/skipped 與 needs_parser/unlock_failures/parse_warnings 清單）→ 回 0
  - 以模組層名稱 import `run_pipeline`、`build_service`、`load_config`（讓測試可 monkeypatch）

- [ ] **Step 1: 寫失敗測試**

`tests/test_cli_run.py`:
```python
import mailquill.cli as cli
from mailquill.config import Config
from mailquill.pipeline import RunResult


def test_main_run_invokes_pipeline_and_returns_zero(monkeypatch, tmp_path, capsys):
    cfg = Config(label="財務")
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)
    monkeypatch.setattr(cli, "build_service", lambda c, t: object())

    captured = {}

    def fake_run(service, cfg_arg, imported_at):
        captured["imported_at"] = imported_at
        return RunResult(fetched=2, matched=1, added=1, skipped=0,
                         needs_parser=["x@unknown.test | 帳單"])

    monkeypatch.setattr(cli, "run_pipeline", fake_run)

    rc = cli.main(["run", "--config", str(tmp_path / "config.yaml")])
    assert rc == 0
    assert captured["imported_at"]  # 有帶入時間字串
    out = capsys.readouterr().out
    assert "added" in out or "新增" in out
    assert "unknown.test" in out  # needs_parser 有列出
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_cli_run.py -v`
Expected: FAIL（`AttributeError`/`SystemExit`：`run` 子指令尚未存在）

- [ ] **Step 3: 追加 `run` CLI 指令**

在 `mailquill/cli.py` 的 import 區追加：
```python
from datetime import datetime

from mailquill.pipeline import run_pipeline
```
（`load_config`、`build_service` 已在計畫 2 的 bootstrap 指令 import；若尚未，請一併確保模組層可取得。）

在 `main` 內、`p_bootstrap` 區塊之後、`args = parser.parse_args(argv)` 之前追加：
```python
    p_run = sub.add_parser(
        "run", help="抓取財務信件、解析、分類並更新 CSV/SQLite"
    )
    p_run.add_argument("--config", default="config.yaml")
```

在 `main` 的分派區段（`bootstrap` 之後）追加：
```python
    if args.command == "run":
        cfg = load_config(args.config)
        service = build_service(cfg.credentials_path, cfg.token_path)
        imported_at = datetime.now().isoformat(timespec="seconds")
        r = run_pipeline(service, cfg, imported_at)
        print(f"run: fetched={r.fetched} matched={r.matched} "
              f"added={r.added} skipped={r.skipped}")
        if r.needs_parser:
            print(f"待補 parser（{len(r.needs_parser)}）：")
            for item in r.needs_parser:
                print(f"  - {item}")
        if r.unlock_failures:
            print(f"PDF 解密失敗（{len(r.unlock_failures)}）：")
            for item in r.unlock_failures:
                print(f"  - {item}")
        if r.parse_warnings:
            print(f"解析警告（{len(r.parse_warnings)}）：")
            for item in r.parse_warnings:
                print(f"  - {item}")
        return 0
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_cli_run.py -v`
Expected: PASS（1 passed）

- [ ] **Step 5: 跑全套測試**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS（計畫 1+2 的 47 + 本計畫新增，全綠）

- [ ] **Step 6: Commit**

```bash
git add mailquill/cli.py tests/test_cli_run.py
git commit -m "feat: add run CLI command wiring the full pipeline"
```

---

## Self-Review

**Spec coverage（本計畫範圍）：**
- rule-based 各家 parser（base + registry + 範例模板，隔離於統一 schema）→ Task 3 ✅
- 正規化成統一 schema（金額/日期清洗、txn_id 去重）→ Task 1 ✅
- PDF 文字抽取 → Task 2 ✅
- 串接抓信→解密→解析→分類→CSV→SQLite 的 `run`→ Task 4 + Task 5 ✅
- 永不靜默丟資料（needs_parser / unlock_failures / parse_warnings 皆可見）→ Task 4（RunResult）+ Task 5（印出）✅
- 後續計畫：report HTML dashboard（計畫 4）；真實銀行 parser 依樣本逐一新增（n2n 後）

**Placeholder scan：** 無 TBD/TODO；每個程式步驟含完整程式碼。範例 parser 明確標記為模板、僅命中測試網域。

**Type consistency：** `RawTxn`、`normalize_amount`/`normalize_date`/`normalize`、`extract_pdf_text`、`Parser`/`register`/`get_parser`/`all_parsers`/`ExampleBankParser`、`RunResult`/`run_pipeline` 跨 task 名稱與簽章一致；CLI 追加沿用既有 `main`/`argparse` 結構與計畫 2 的模組層 import 慣例。
