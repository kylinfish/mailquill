"""串接：抓信 → 解密 PDF → parser → 正規化 → 分類 → CSV → SQLite。"""
from __future__ import annotations

from dataclasses import dataclass, field

from mailquill.config import Config, load_passwords
from mailquill.rules import load_rules, matches
from mailquill.gmail_client import (
    list_labels_messages, extract_message, get_message_metadata,
)
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
    missing_labels: list[str] = field(default_factory=list)
    fetch_failures: list[str] = field(default_factory=list)


def _is_pdf(att) -> bool:
    return att.mime_type == "application/pdf" or att.filename.lower().endswith(".pdf")


def run_pipeline(service, cfg: Config, imported_at: str,
                 query: str | None = None, on_progress=None) -> RunResult:
    result = RunResult()
    rules = load_rules(cfg.rules_path)
    passwords = load_passwords(cfg.passwords_path)
    category_rules = load_categories(cfg.categories_path)

    ids, missing = list_labels_messages(service, cfg.label_names(), query=query)
    result.missing_labels = missing
    result.fetched = len(ids)

    collected = []
    total = len(ids)
    for i, mid in enumerate(ids):
        if on_progress:
            on_progress(i + 1, total)
        # 先用輕量標頭過濾，不符合規則的就不下載完整信件與附件。
        # 單封信抓取失敗(網路逾時等)不應中斷整批 — 記錄後跳過，重跑可靠去重補回。
        try:
            sender, subject, _ = get_message_metadata(service, mid)
        except Exception as e:
            result.fetch_failures.append(f"{mid}: metadata: {e}")
            continue
        if not matches(rules, sender, subject):
            continue
        result.matched += 1

        try:
            msg = extract_message(service, mid)
        except Exception as e:
            result.fetch_failures.append(f"{mid}: fetch: {e}")
            continue
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
        for seq, raw in enumerate(parser.parse(msg, pdf_texts)):
            txn = normalize(raw, msg.msg_id, source_type, msg.msg_id, imported_at, seq=seq)
            if txn.amount == "":
                result.parse_warnings.append(f"{msg.msg_id}:{raw.merchant_raw}")
            collected.append(apply_categories(txn, category_rules))

    append_res = append_transactions(cfg.csv_path, collected)
    result.added = append_res.added
    result.skipped = append_res.skipped
    rebuild_sqlite(cfg.csv_path, cfg.db_path)
    return result
