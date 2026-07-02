# mailquill 計畫 2：Ingestion I/O 層 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 mailquill 與外界互動的輸入層：設定載入、加密 PDF 解密、Gmail 收信（OAuth + 抓信 + 解析 MIME）、收信規則（rules.yaml 載入/比對）、以及由既有 Gmail Label 產生 rules 草稿的 bootstrap 與其 CLI 指令。

**Architecture:** 純本地、純 rule-based。所有「可測純函式」與「對外 I/O」分離：Gmail 原始 payload 的解析是純函式（fixture 可測），OAuth/網路呼叫是薄包裝（n2n 驗收時才接真帳號）。PDF 解密用 `pikepdf` 試一份本地密碼清單。本計畫不解析交易、不寫 CSV（那是計畫 3）。

**Tech Stack:** Python 3.13、`pikepdf`、`google-api-python-client`、`google-auth-oauthlib`、`google-auth-httplib2`、`PyYAML`、`pytest`。專案已有 `.venv`，所有指令用 `.venv/bin/python` 執行。

## Global Constraints

- 全本地處理；Gmail 僅 `gmail.readonly` scope（唯讀，不改信箱）。
- 純 rule-based，不使用任何 LLM。
- 機密檔案絕不進 git：`credentials.json`、`token.json`、`passwords.txt`（已在 `.gitignore`）。
- 可測純函式與對外 I/O 必須分離；OAuth/網路呼叫不寫單元測試（標記為 n2n 驗收項）。
- 所有測試用 `.venv/bin/python -m pytest` 執行。
- 不靜默丟資料：PDF 解不開回傳失敗結果（含原因），不拋例外中斷整批。
- `rules.yaml`、`config.yaml` 為非機密、可進 git 的範例；實際使用者檔案由使用者維護。

---

## File Structure

```
mailquill/
  config.py            # Config dataclass、load_config、load_passwords
  pdf_unlocker.py      # unlock_pdf、PdfUnlockResult
  rules.py             # Rules dataclass、load_rules、save_rules、matches
  gmail_message.py     # EmailMessage/Attachment 資料模型 + 純 payload 解析
  gmail_client.py      # build_service、list_label_messages、extract_message（用 gmail_message 解析）
  bootstrap.py         # sender 網域聚合、bootstrap_rules
  cli.py               # 追加 `bootstrap` subcommand（modify）
config.example.yaml    # 設定範例（進 git）
tests/
  test_config.py
  test_pdf_unlocker.py
  test_rules.py
  test_gmail_message.py
  test_gmail_client.py
  test_bootstrap.py
```

---

### Task 1: 設定載入與依賴

**Files:**
- Create: `mailquill/config.py`
- Create: `config.example.yaml`
- Modify: `pyproject.toml`（追加依賴與 pytest 設定）
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `Config` dataclass，欄位（皆有預設，`label` 必填）：
    `label: str`, `csv_path="transactions.csv"`, `db_path="mailquill.db"`,
    `categories_path="categories.yaml"`, `rules_path="rules.yaml"`,
    `raw_dir="raw"`, `passwords_path="passwords.txt"`,
    `credentials_path="credentials.json"`, `token_path="token.json"`
  - `load_config(path: str = "config.yaml") -> Config` — 讀 YAML，未提供的欄位用預設；`label` 缺失則 `raise ValueError`
  - `load_passwords(path: str) -> list[str]` — 逐行讀取、`strip`、略過空行與 `#` 開頭註解；檔案不存在回 `[]`

- [ ] **Step 1: 寫失敗測試**

`tests/test_config.py`:
```python
import textwrap
import pytest

from mailquill.config import Config, load_config, load_passwords


def test_load_config_applies_defaults(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("label: 財務\n", encoding="utf-8")
    cfg = load_config(str(p))
    assert cfg.label == "財務"
    assert cfg.csv_path == "transactions.csv"
    assert cfg.db_path == "mailquill.db"
    assert cfg.passwords_path == "passwords.txt"


def test_load_config_overrides(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent("""
        label: 財務
        csv_path: /data/t.csv
        db_path: /data/t.db
    """), encoding="utf-8")
    cfg = load_config(str(p))
    assert cfg.csv_path == "/data/t.csv"
    assert cfg.db_path == "/data/t.db"


def test_load_config_missing_label_raises(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("csv_path: x.csv\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(str(p))


def test_load_passwords_filters_blank_and_comments(tmp_path):
    p = tmp_path / "passwords.txt"
    p.write_text("A1234\n\n# comment\n  B5678  \n", encoding="utf-8")
    assert load_passwords(str(p)) == ["A1234", "B5678"]


def test_load_passwords_missing_file_returns_empty(tmp_path):
    assert load_passwords(str(tmp_path / "none.txt")) == []
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'mailquill.config'`）

- [ ] **Step 3: 追加依賴與 pytest 設定**

在 `pyproject.toml` 的 `dependencies` 陣列改為：
```toml
dependencies = [
    "PyYAML>=6.0",
    "pikepdf>=9.0",
    "pdfplumber>=0.11",
    "google-api-python-client>=2.0",
    "google-auth-oauthlib>=1.0",
    "google-auth-httplib2>=0.2",
]
```
並在檔案末尾追加：
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 4: 實作 config**

`mailquill/config.py`:
```python
"""設定與密碼清單載入。"""
from __future__ import annotations

import os
from dataclasses import dataclass

import yaml


@dataclass
class Config:
    label: str
    csv_path: str = "transactions.csv"
    db_path: str = "mailquill.db"
    categories_path: str = "categories.yaml"
    rules_path: str = "rules.yaml"
    raw_dir: str = "raw"
    passwords_path: str = "passwords.txt"
    credentials_path: str = "credentials.json"
    token_path: str = "token.json"


def load_config(path: str = "config.yaml") -> Config:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if "label" not in data or not data["label"]:
        raise ValueError("config 缺少必填欄位 'label'")
    allowed = {f for f in Config.__dataclass_fields__}
    kwargs = {k: v for k, v in data.items() if k in allowed}
    return Config(**kwargs)


def load_passwords(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith("#"):
                out.append(s)
    return out
```

`config.example.yaml`:
```yaml
# 複製成 config.yaml 後修改。config.yaml 與下列機密檔不進 git。
label: "財務"              # 要掃描/抓取的 Gmail Label 名稱
csv_path: "transactions.csv"
db_path: "mailquill.db"
categories_path: "categories.yaml"
rules_path: "rules.yaml"
raw_dir: "raw"
passwords_path: "passwords.txt"     # 每行一組 PDF 密碼，# 開頭為註解
credentials_path: "credentials.json"  # Google Cloud OAuth client（自行下載）
token_path: "token.json"              # 首次授權後自動產生
```

- [ ] **Step 5: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS（5 passed）

- [ ] **Step 6: Commit**

```bash
git add mailquill/config.py config.example.yaml pyproject.toml tests/test_config.py
git commit -m "feat: add config and password-list loading"
```

---

### Task 2: 加密 PDF 解密

**Files:**
- Create: `mailquill/pdf_unlocker.py`
- Test: `tests/test_pdf_unlocker.py`

**Interfaces:**
- Produces:
  - `PdfUnlockResult` dataclass：`ok: bool`, `data: bytes | None`, `password_used: str | None`, `error: str | None`
  - `unlock_pdf(pdf_bytes: bytes, passwords: list[str]) -> PdfUnlockResult`
    - 未加密的 PDF：`ok=True`, `data=<原樣 PDF bytes>`, `password_used=None`
    - 加密且某密碼成功：`ok=True`, `data=<解密後 PDF bytes>`, `password_used=<該密碼>`
    - 加密且全部密碼失敗（或清單為空）：`ok=False`, `data=None`, `error=<說明>`
    - 永不拋例外

- [ ] **Step 1: 寫失敗測試**

`tests/test_pdf_unlocker.py`:
```python
import io

import pikepdf

from mailquill.pdf_unlocker import unlock_pdf, PdfUnlockResult


def _plain_pdf() -> bytes:
    pdf = pikepdf.new()
    pdf.add_blank_page()
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _encrypted_pdf(password: str) -> bytes:
    pdf = pikepdf.new()
    pdf.add_blank_page()
    buf = io.BytesIO()
    pdf.save(buf, encryption=pikepdf.Encryption(owner=password, user=password))
    return buf.getvalue()


def test_plain_pdf_returns_ok_without_password():
    res = unlock_pdf(_plain_pdf(), ["whatever"])
    assert res.ok is True
    assert res.password_used is None
    assert res.data is not None
    # 解出的 bytes 仍可被開啟
    with pikepdf.open(io.BytesIO(res.data)) as p:
        assert len(p.pages) == 1


def test_encrypted_pdf_unlocks_with_correct_password():
    res = unlock_pdf(_encrypted_pdf("SECRET1"), ["WRONG", "SECRET1"])
    assert res.ok is True
    assert res.password_used == "SECRET1"
    with pikepdf.open(io.BytesIO(res.data)) as p:  # 已解密，免密碼可開
        assert len(p.pages) == 1


def test_encrypted_pdf_all_passwords_fail():
    res = unlock_pdf(_encrypted_pdf("SECRET1"), ["NOPE", "ALSO_NO"])
    assert res.ok is False
    assert res.data is None
    assert res.error


def test_empty_password_list_on_encrypted_fails():
    res = unlock_pdf(_encrypted_pdf("SECRET1"), [])
    assert res.ok is False
    assert res.error
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_pdf_unlocker.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 實作 pdf_unlocker**

`mailquill/pdf_unlocker.py`:
```python
"""用本地密碼清單解開加密 PDF。永不拋例外。"""
from __future__ import annotations

import io
from dataclasses import dataclass

import pikepdf


@dataclass
class PdfUnlockResult:
    ok: bool
    data: bytes | None
    password_used: str | None
    error: str | None


def _save_decrypted(pdf: pikepdf.Pdf) -> bytes:
    out = io.BytesIO()
    pdf.save(out)  # 不帶 encryption 參數 → 存成未加密
    return out.getvalue()


def unlock_pdf(pdf_bytes: bytes, passwords: list[str]) -> PdfUnlockResult:
    # 先嘗試無密碼開啟（未加密的情況）
    try:
        with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
            return PdfUnlockResult(True, _save_decrypted(pdf), None, None)
    except pikepdf.PasswordError:
        pass
    except Exception as e:  # 壞檔等
        return PdfUnlockResult(False, None, None, f"無法開啟 PDF: {e}")

    # 加密：逐一嘗試密碼
    for pw in passwords:
        try:
            with pikepdf.open(io.BytesIO(pdf_bytes), password=pw) as pdf:
                return PdfUnlockResult(True, _save_decrypted(pdf), pw, None)
        except pikepdf.PasswordError:
            continue
        except Exception as e:
            return PdfUnlockResult(False, None, None, f"解密時出錯: {e}")

    return PdfUnlockResult(False, None, None, "密碼清單皆無法解開此加密 PDF")
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_pdf_unlocker.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add mailquill/pdf_unlocker.py tests/test_pdf_unlocker.py
git commit -m "feat: add PDF unlocker with password-list attempts"
```

---

### Task 3: 收信規則 rules.yaml

**Files:**
- Create: `mailquill/rules.py`
- Test: `tests/test_rules.py`

**Interfaces:**
- Produces:
  - `Rules` dataclass：`senders: list[str]`, `subject_keywords: list[str]`
  - `load_rules(path: str) -> Rules` — 讀 YAML；缺欄位用空 list；檔案不存在回 `Rules([], [])`
  - `save_rules(path: str, rules: Rules) -> None` — 寫出 YAML（`senders`、`subject_keywords` 兩個 key）
  - `matches(rules: Rules, sender: str, subject: str) -> bool` — `sender` 含任一 `senders` 子字串，或 `subject` 含任一 `subject_keywords` 子字串即 `True`

- [ ] **Step 1: 寫失敗測試**

`tests/test_rules.py`:
```python
from mailquill.rules import Rules, load_rules, save_rules, matches


def test_save_then_load_roundtrip(tmp_path):
    path = str(tmp_path / "rules.yaml")
    rules = Rules(senders=["@cathaybk.com.tw"], subject_keywords=["帳單", "消費"])
    save_rules(path, rules)
    loaded = load_rules(path)
    assert loaded == rules


def test_load_missing_file_returns_empty(tmp_path):
    assert load_rules(str(tmp_path / "none.yaml")) == Rules([], [])


def test_matches_by_sender():
    rules = Rules(senders=["@cathaybk.com.tw"], subject_keywords=[])
    assert matches(rules, "信用卡 <ebill@cathaybk.com.tw>", "任意主旨") is True
    assert matches(rules, "noise <a@other.com>", "任意主旨") is False


def test_matches_by_subject_keyword():
    rules = Rules(senders=[], subject_keywords=["帳單"])
    assert matches(rules, "a@other.com", "您的電子帳單已產生") is True
    assert matches(rules, "a@other.com", "促銷通知") is False


def test_matches_requires_some_rule():
    rules = Rules(senders=[], subject_keywords=[])
    assert matches(rules, "a@b.com", "任何主旨") is False
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_rules.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 實作 rules**

`mailquill/rules.py`:
```python
"""收信比對規則的載入、儲存與比對。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml


@dataclass
class Rules:
    senders: list[str] = field(default_factory=list)
    subject_keywords: list[str] = field(default_factory=list)


def load_rules(path: str) -> Rules:
    if not os.path.exists(path):
        return Rules([], [])
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Rules(
        senders=list(data.get("senders", []) or []),
        subject_keywords=list(data.get("subject_keywords", []) or []),
    )


def save_rules(path: str, rules: Rules) -> None:
    data = {"senders": rules.senders, "subject_keywords": rules.subject_keywords}
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def matches(rules: Rules, sender: str, subject: str) -> bool:
    if any(s in sender for s in rules.senders):
        return True
    if any(k in subject for k in rules.subject_keywords):
        return True
    return False
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_rules.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add mailquill/rules.py tests/test_rules.py
git commit -m "feat: add rules load/save/match"
```

---

### Task 4: Gmail 資料模型與純 payload 解析

**Files:**
- Create: `mailquill/gmail_message.py`
- Test: `tests/test_gmail_message.py`

**Interfaces:**
- Produces:
  - `Attachment` dataclass：`filename: str`, `mime_type: str`, `data: bytes`
  - `EmailMessage` dataclass：`msg_id: str`, `sender: str`, `subject: str`, `date: str`, `body_text: str`, `attachments: list[Attachment]`
  - `decode_b64url(data: str) -> bytes` — base64url 解碼（自動補 padding）
  - `header_value(headers: list[dict], name: str) -> str` — 不分大小寫取 header value，找不到回 `""`
  - `walk_payload(payload: dict) -> tuple[list[str], list[dict]]` — 遞迴走訪 MIME；回傳
    `(text/plain 字串清單, 附件規格清單)`。附件規格 dict：
    `{"filename": str, "mime_type": str, "attachment_id": str|None, "inline_data": str|None}`

- [ ] **Step 1: 寫失敗測試**

`tests/test_gmail_message.py`:
```python
import base64

from mailquill.gmail_message import (
    Attachment, EmailMessage, decode_b64url, header_value, walk_payload,
)


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


def test_decode_b64url_handles_missing_padding():
    assert decode_b64url(_b64("帳單明細")) == "帳單明細".encode("utf-8")


def test_header_value_case_insensitive():
    headers = [{"name": "From", "value": "a@b.com"}, {"name": "Subject", "value": "S"}]
    assert header_value(headers, "from") == "a@b.com"
    assert header_value(headers, "SUBJECT") == "S"
    assert header_value(headers, "Cc") == ""


def test_walk_payload_collects_text_and_attachments():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "multipart/alternative", "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("消費 1200 元")}},
                {"mimeType": "text/html", "body": {"data": _b64("<p>ignored</p>")}},
            ]},
            {"mimeType": "application/pdf", "filename": "stmt.pdf",
             "body": {"attachmentId": "att-1", "size": 999}},
        ],
    }
    texts, atts = walk_payload(payload)
    assert texts == ["消費 1200 元"]
    assert atts == [{
        "filename": "stmt.pdf", "mime_type": "application/pdf",
        "attachment_id": "att-1", "inline_data": None,
    }]


def test_walk_payload_inline_attachment_data():
    payload = {
        "mimeType": "application/pdf", "filename": "x.pdf",
        "body": {"data": _b64("PDFDATA")},
    }
    texts, atts = walk_payload(payload)
    assert texts == []
    assert atts[0]["inline_data"] == _b64("PDFDATA")
    assert atts[0]["attachment_id"] is None


def test_dataclasses_construct():
    msg = EmailMessage(
        msg_id="m1", sender="a@b.com", subject="S", date="D",
        body_text="B", attachments=[Attachment("f.pdf", "application/pdf", b"x")],
    )
    assert msg.attachments[0].data == b"x"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_gmail_message.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 實作 gmail_message**

`mailquill/gmail_message.py`:
```python
"""Gmail 訊息資料模型與純 payload 解析（無網路）。"""
from __future__ import annotations

import base64
from dataclasses import dataclass


@dataclass
class Attachment:
    filename: str
    mime_type: str
    data: bytes


@dataclass
class EmailMessage:
    msg_id: str
    sender: str
    subject: str
    date: str
    body_text: str
    attachments: list[Attachment]


def decode_b64url(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded)


def header_value(headers: list[dict], name: str) -> str:
    target = name.lower()
    for h in headers:
        if h.get("name", "").lower() == target:
            return h.get("value", "")
    return ""


def walk_payload(payload: dict) -> tuple[list[str], list[dict]]:
    texts: list[str] = []
    attachments: list[dict] = []

    def recurse(part: dict) -> None:
        if part.get("parts"):
            for sub in part["parts"]:
                recurse(sub)
            return
        mime = part.get("mimeType", "")
        filename = part.get("filename", "")
        body = part.get("body", {}) or {}
        if filename:
            attachments.append({
                "filename": filename,
                "mime_type": mime,
                "attachment_id": body.get("attachmentId"),
                "inline_data": body.get("data"),
            })
        elif mime == "text/plain" and body.get("data"):
            texts.append(decode_b64url(body["data"]).decode("utf-8", errors="replace"))

    recurse(payload)
    return texts, attachments
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_gmail_message.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add mailquill/gmail_message.py tests/test_gmail_message.py
git commit -m "feat: add Gmail message model and pure payload parsing"
```

---

### Task 5: Gmail 服務函式（service-backed）

**Files:**
- Create: `mailquill/gmail_client.py`
- Test: `tests/test_gmail_client.py`

**Interfaces:**
- Consumes: `gmail_message`（`EmailMessage`, `Attachment`, `decode_b64url`, `header_value`, `walk_payload`）
- Produces:
  - `SCOPES: list[str]` = `["https://www.googleapis.com/auth/gmail.readonly"]`
  - `build_service(credentials_path: str, token_path: str)` — OAuth 流程，回傳 Gmail API service（**整合用、不寫單元測試**；n2n 驗收項）
  - `label_id(service, label_name: str, user_id: str = "me") -> str` — 由名稱找 label id，找不到 `raise ValueError`
  - `list_label_messages(service, label_name: str, query: str | None = None, user_id: str = "me") -> list[str]` — 回傳該 label 全部 message id（處理分頁）
  - `extract_message(service, msg_id: str, user_id: str = "me") -> EmailMessage` — 取 full message、解析 header/body、解析並下載附件（inline 直接用、否則用 attachmentId 下載）

說明：`service` 為注入物件（googleapiclient resource）。測試用 fake service 提供 `.users().messages().list/get/attachments().get()` 與 `.users().labels().list()` 的鏈式呼叫。

- [ ] **Step 1: 寫失敗測試**

`tests/test_gmail_client.py`:
```python
import base64

import pytest

from mailquill.gmail_client import label_id, list_label_messages, extract_message


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


class _Exec:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


class _Messages:
    def __init__(self, fake):
        self._fake = fake

    def list(self, userId, labelIds=None, q=None, pageToken=None):
        return _Exec(self._fake.list_pages[pageToken])

    def get(self, userId, id, format=None):
        return _Exec(self._fake.messages[id])

    def attachments(self):
        return self

    # attachments().get(...)
    def get_attachment(self, userId, messageId, id):
        return _Exec({"data": self._fake.attachments[id]})


class _AttachmentsProxy:
    def __init__(self, fake):
        self._fake = fake

    def get(self, userId, messageId, id):
        return _Exec({"data": self._fake.attachments[id]})


class _Labels:
    def __init__(self, fake):
        self._fake = fake

    def list(self, userId):
        return _Exec({"labels": self._fake.labels})


class _MessagesResource:
    def __init__(self, fake):
        self._fake = fake

    def list(self, userId, labelIds=None, q=None, pageToken=None):
        return _Exec(self._fake.list_pages[pageToken])

    def get(self, userId, id, format=None):
        return _Exec(self._fake.messages[id])

    def attachments(self):
        return _AttachmentsProxy(self._fake)


class _Users:
    def __init__(self, fake):
        self._fake = fake

    def messages(self):
        return _MessagesResource(self._fake)

    def labels(self):
        return _Labels(self._fake)


class FakeService:
    def __init__(self):
        self.labels = []
        self.list_pages = {}     # pageToken -> response dict
        self.messages = {}       # msg_id -> full message dict
        self.attachments = {}    # attachment_id -> base64url data

    def users(self):
        return _Users(self)


def test_label_id_found_and_missing():
    svc = FakeService()
    svc.labels = [{"id": "L1", "name": "財務"}, {"id": "L2", "name": "其他"}]
    assert label_id(svc, "財務") == "L1"
    with pytest.raises(ValueError):
        label_id(svc, "不存在")


def test_list_label_messages_paginates():
    svc = FakeService()
    svc.labels = [{"id": "L1", "name": "財務"}]
    svc.list_pages = {
        None: {"messages": [{"id": "m1"}, {"id": "m2"}], "nextPageToken": "p2"},
        "p2": {"messages": [{"id": "m3"}]},
    }
    assert list_label_messages(svc, "財務") == ["m1", "m2", "m3"]


def test_extract_message_parses_body_and_downloads_attachment():
    svc = FakeService()
    svc.messages = {
        "m1": {
            "payload": {
                "headers": [
                    {"name": "From", "value": "帳單 <ebill@cathaybk.com.tw>"},
                    {"name": "Subject", "value": "電子帳單"},
                    {"name": "Date", "value": "Mon, 01 Jun 2026 10:00:00 +0800"},
                ],
                "mimeType": "multipart/mixed",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _b64("消費明細")}},
                    {"mimeType": "application/pdf", "filename": "stmt.pdf",
                     "body": {"attachmentId": "att-1"}},
                ],
            }
        }
    }
    svc.attachments = {"att-1": _b64("PDFBYTES")}
    msg = extract_message(svc, "m1")
    assert msg.msg_id == "m1"
    assert msg.sender == "帳單 <ebill@cathaybk.com.tw>"
    assert msg.subject == "電子帳單"
    assert msg.body_text == "消費明細"
    assert len(msg.attachments) == 1
    assert msg.attachments[0].filename == "stmt.pdf"
    assert msg.attachments[0].data == b"PDFBYTES"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_gmail_client.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 實作 gmail_client**

`mailquill/gmail_client.py`:
```python
"""Gmail API 服務函式。純解析委派給 gmail_message。"""
from __future__ import annotations

import os

from mailquill.gmail_message import (
    Attachment, EmailMessage, decode_b64url, header_value, walk_payload,
)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def build_service(credentials_path: str, token_path: str):
    """OAuth 授權並回傳 Gmail service。整合用，不寫單元測試。"""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def label_id(service, label_name: str, user_id: str = "me") -> str:
    resp = service.users().labels().list(userId=user_id).execute()
    for lab in resp.get("labels", []):
        if lab.get("name") == label_name:
            return lab["id"]
    raise ValueError(f"找不到 Gmail label: {label_name}")


def list_label_messages(service, label_name: str, query: str | None = None,
                        user_id: str = "me") -> list[str]:
    lid = label_id(service, label_name, user_id)
    ids: list[str] = []
    page_token = None
    while True:
        resp = service.users().messages().list(
            userId=user_id, labelIds=[lid], q=query, pageToken=page_token,
        ).execute()
        ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return ids


def extract_message(service, msg_id: str, user_id: str = "me") -> EmailMessage:
    msg = service.users().messages().get(
        userId=user_id, id=msg_id, format="full",
    ).execute()
    payload = msg.get("payload", {}) or {}
    headers = payload.get("headers", []) or []
    texts, att_specs = walk_payload(payload)

    attachments: list[Attachment] = []
    for spec in att_specs:
        if spec["inline_data"]:
            data = decode_b64url(spec["inline_data"])
        elif spec["attachment_id"]:
            resp = service.users().messages().attachments().get(
                userId=user_id, messageId=msg_id, id=spec["attachment_id"],
            ).execute()
            data = decode_b64url(resp["data"])
        else:
            data = b""
        attachments.append(Attachment(spec["filename"], spec["mime_type"], data))

    return EmailMessage(
        msg_id=msg_id,
        sender=header_value(headers, "From"),
        subject=header_value(headers, "Subject"),
        date=header_value(headers, "Date"),
        body_text="\n".join(texts),
        attachments=attachments,
    )
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_gmail_client.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add mailquill/gmail_client.py tests/test_gmail_client.py
git commit -m "feat: add Gmail service functions (list/extract, OAuth build)"
```

---

### Task 6: bootstrap 與 `bootstrap` CLI 指令

**Files:**
- Create: `mailquill/bootstrap.py`
- Modify: `mailquill/cli.py`（追加 `bootstrap` subcommand）
- Test: `tests/test_bootstrap.py`

**Interfaces:**
- Consumes: `gmail_message.EmailMessage`；`gmail_client.list_label_messages`, `extract_message`；`rules.Rules`, `save_rules`；`config.load_config`
- Produces:
  - `sender_domain(sender: str) -> str` — 由 `"Name <a@b.com>"` 或 `"a@b.com"` 取出 `"@b.com"`（小寫）；無 `@` 回 `""`
  - `aggregate_senders(messages: list[EmailMessage]) -> list[str]` — 去重後、依首次出現順序排列的網域清單（排除空字串）
  - `DEFAULT_SUBJECT_KEYWORDS: list[str]` = `["帳單", "消費", "交易", "明細", "刷卡", "扣款", "繳費"]`
  - `bootstrap_rules(service, label_name: str) -> Rules` — 掃描 label 全部信件，聚合寄件網域為 `senders`，`subject_keywords` 用 `DEFAULT_SUBJECT_KEYWORDS`
  - CLI：`mailquill bootstrap [--config PATH]` — `load_config` 取 label 與 `credentials/token/rules` 路徑，`build_service` → `bootstrap_rules` → `save_rules`，印出找到的寄件網域數與輸出路徑，回 0

- [ ] **Step 1: 寫失敗測試**

`tests/test_bootstrap.py`:
```python
from mailquill.gmail_message import EmailMessage
from mailquill.bootstrap import (
    sender_domain, aggregate_senders, bootstrap_rules, DEFAULT_SUBJECT_KEYWORDS,
)


def _msg(sender):
    return EmailMessage(msg_id="x", sender=sender, subject="", date="",
                        body_text="", attachments=[])


def test_sender_domain_extracts_domain():
    assert sender_domain("帳單 <ebill@cathaybk.com.tw>") == "@cathaybk.com.tw"
    assert sender_domain("noreply@CTBCbank.com") == "@ctbcbank.com"
    assert sender_domain("no-at-sign") == ""


def test_aggregate_senders_dedups_in_order():
    msgs = [
        _msg("a <x@cathaybk.com.tw>"),
        _msg("b <y@ctbcbank.com>"),
        _msg("c <z@cathaybk.com.tw>"),
        _msg("bad-sender"),
    ]
    assert aggregate_senders(msgs) == ["@cathaybk.com.tw", "@ctbcbank.com"]


def test_bootstrap_rules_uses_fake_gmail(monkeypatch):
    import mailquill.bootstrap as b

    monkeypatch.setattr(b, "list_label_messages", lambda service, label: ["m1", "m2"])
    canned = {
        "m1": _msg("a <x@cathaybk.com.tw>"),
        "m2": _msg("b <y@ctbcbank.com>"),
    }
    monkeypatch.setattr(b, "extract_message", lambda service, mid: canned[mid])

    rules = bootstrap_rules(service=object(), label_name="財務")
    assert rules.senders == ["@cathaybk.com.tw", "@ctbcbank.com"]
    assert rules.subject_keywords == DEFAULT_SUBJECT_KEYWORDS
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_bootstrap.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 實作 bootstrap**

`mailquill/bootstrap.py`:
```python
"""由既有 Gmail Label 聚合寄件網域，產生 rules 草稿。"""
from __future__ import annotations

import re

from mailquill.gmail_message import EmailMessage
from mailquill.gmail_client import list_label_messages, extract_message
from mailquill.rules import Rules

DEFAULT_SUBJECT_KEYWORDS = ["帳單", "消費", "交易", "明細", "刷卡", "扣款", "繳費"]

_EMAIL_RE = re.compile(r"[^\s<]+@([^\s>]+)")


def sender_domain(sender: str) -> str:
    m = _EMAIL_RE.search(sender)
    if not m:
        return ""
    return "@" + m.group(1).lower()


def aggregate_senders(messages: list[EmailMessage]) -> list[str]:
    seen: list[str] = []
    for msg in messages:
        dom = sender_domain(msg.sender)
        if dom and dom not in seen:
            seen.append(dom)
    return seen


def bootstrap_rules(service, label_name: str) -> Rules:
    ids = list_label_messages(service, label_name)
    messages = [extract_message(service, mid) for mid in ids]
    return Rules(
        senders=aggregate_senders(messages),
        subject_keywords=list(DEFAULT_SUBJECT_KEYWORDS),
    )
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_bootstrap.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 追加 `bootstrap` CLI 指令**

在 `mailquill/cli.py` 的 import 區追加：
```python
from mailquill.config import load_config
from mailquill.bootstrap import bootstrap_rules
from mailquill.gmail_client import build_service
from mailquill.rules import save_rules
```

在 `main` 內、`p_rebuild` 區塊定義之後、`args = parser.parse_args(argv)` 之前追加：
```python
    p_bootstrap = sub.add_parser(
        "bootstrap", help="掃描 Gmail Label 產生 rules.yaml 草稿供確認"
    )
    p_bootstrap.add_argument("--config", default="config.yaml")
```

在 `main` 的分派區段（`if args.command == "rebuild":` 之後）追加：
```python
    if args.command == "bootstrap":
        cfg = load_config(args.config)
        service = build_service(cfg.credentials_path, cfg.token_path)
        rules = bootstrap_rules(service, cfg.label)
        save_rules(cfg.rules_path, rules)
        print(f"bootstrap: 找到 {len(rules.senders)} 個寄件網域 -> {cfg.rules_path}")
        print("請檢查並編輯 rules.yaml 後再執行 run。")
        return 0
```

- [ ] **Step 6: 跑全套測試**

Run: `.venv/bin/python -m pytest -v`
Expected: PASS（計畫 1 的 18 + 本計畫新增，全綠）

- [ ] **Step 7: Commit**

```bash
git add mailquill/bootstrap.py mailquill/cli.py tests/test_bootstrap.py
git commit -m "feat: add bootstrap (label -> rules draft) and CLI command"
```

---

## Self-Review

**Spec coverage（本計畫範圍）：**
- 設定/密碼清單載入 → Task 1 ✅
- 加密 PDF 解密（密碼清單、永不中斷）→ Task 2 ✅
- 收信規則 rules.yaml（載入/儲存/比對）→ Task 3 ✅
- Gmail 收信：唯讀 scope、抓信、解析內文與附件 → Task 4（純解析）+ Task 5（service）✅
- bootstrap：掃 Label 聚合寄件者 → rules 草稿 + 使用者確認 → Task 6 ✅
- 後續計畫：parsers / normalizer / pipeline / `run`（計畫 3）；report（計畫 4）

**Placeholder scan：** 無 TBD/TODO；每個程式步驟含完整程式碼。`build_service` 為網路/OAuth glue，明確標記為整合（n2n）驗收項、不寫單元測試——其餘函式皆有測試。

**Type consistency：** `Config`/`load_config`/`load_passwords`、`PdfUnlockResult`/`unlock_pdf`、`Rules`/`load_rules`/`save_rules`/`matches`、`Attachment`/`EmailMessage`/`decode_b64url`/`header_value`/`walk_payload`、`label_id`/`list_label_messages`/`extract_message`、`sender_domain`/`aggregate_senders`/`bootstrap_rules`/`DEFAULT_SUBJECT_KEYWORDS` 跨 task 名稱與簽章一致；CLI 追加沿用計畫 1 既有 `main`/`argparse` 結構。
