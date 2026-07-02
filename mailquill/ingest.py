"""本地 PDF 匯入：把手動下載的帳單（如富邦需連結下載者）走同一條
解密→parser→正規化→分類→CSV→SQLite 流程，不經 Gmail。

與 `run` 共用 txn_id 去重，所以同一份帳單即使先前已由 Gmail 抓過，
再以本地檔匯入也不會重複入帳。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from mailquill.config import Config, load_passwords
from mailquill.pdf_unlocker import unlock_pdf
from mailquill.pdf_text import extract_pdf_text
from mailquill.parsers import all_parsers
from mailquill.normalizer import normalize
from mailquill.categorizer import load_categories, apply_categories
from mailquill.store import append_transactions, rebuild_sqlite
from mailquill.gmail_message import EmailMessage


@dataclass
class IngestResult:
    files: int = 0
    added: int = 0
    skipped: int = 0
    parsed: list[str] = field(default_factory=list)         # "檔名 -> Bank (n筆)"
    unlock_failures: list[str] = field(default_factory=list)
    needs_parser: list[str] = field(default_factory=list)   # 無 parser 能解析的檔
    parse_warnings: list[str] = field(default_factory=list)


def collect_pdfs(paths: list[str]) -> list[str]:
    """把檔案/資料夾參數展開成 PDF 檔清單（資料夾遞迴、排序）。"""
    out: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs.sort()          # 子目錄也排序，確保跨平台/跨次序一致
                for f in sorted(files):
                    if f.lower().endswith(".pdf"):
                        out.append(os.path.join(root, f))
        elif p.lower().endswith(".pdf"):
            out.append(p)
    return out


def select_parser(text: str, bank: str | None = None):
    """依 PDF 文字內容挑 parser（無寄件者可比對時用）。

    對每個 parser 試解，取解析出最多筆者；bank 有給時只限該銀行的 parser。
    回傳 (parser, raw_txns)；都解不出回 (None, [])。
    """
    msg = EmailMessage("", "", "", "", "", [])
    best = None
    best_txns: list = []
    for parser in all_parsers():
        if bank and bank.lower() not in parser.bank.lower():
            continue
        try:
            txns = parser.parse(msg, [text])
        except Exception:
            txns = []
        if len(txns) > len(best_txns):
            best, best_txns = parser, txns
    return best, best_txns


def ingest_paths(paths: list[str], cfg: Config, imported_at: str,
                 bank: str | None = None, on_progress=None) -> IngestResult:
    result = IngestResult()
    passwords = load_passwords(cfg.passwords_path)
    category_rules = load_categories(cfg.categories_path)
    pdfs = collect_pdfs(paths)
    result.files = len(pdfs)

    collected = []
    for i, path in enumerate(pdfs):
        if on_progress:
            on_progress(i + 1, len(pdfs))
        name = os.path.basename(path)
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError as e:
            result.unlock_failures.append(f"{name}: 讀檔失敗 {e}")
            continue
        res = unlock_pdf(raw, passwords)
        if not res.ok:
            result.unlock_failures.append(f"{name}: {res.error}")
            continue
        text = extract_pdf_text(res.data)
        parser, raws = select_parser(text, bank)
        if not raws:
            result.needs_parser.append(name)
            continue
        result.parsed.append(f"{name} -> {parser.bank} ({len(raws)}筆)")
        for seq, raw_txn in enumerate(raws):
            txn = normalize(raw_txn, name, "pdf", path, imported_at, seq=seq)
            if txn.amount == "":
                result.parse_warnings.append(f"{name}:{raw_txn.merchant_raw}")
            collected.append(apply_categories(txn, category_rules))

    append_res = append_transactions(cfg.csv_path, collected)
    result.added = append_res.added
    result.skipped = append_res.skipped
    rebuild_sqlite(cfg.csv_path, cfg.db_path)
    return result
