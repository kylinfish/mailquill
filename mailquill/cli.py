"""mailquill CLI。已實作 `rebuild`、`bootstrap`、`run`；`report` 由後續計畫追加。"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import tempfile
from datetime import datetime

from mailquill.categorizer import apply_categories, load_categories
from mailquill.schema import FIELDS
from mailquill.store import read_transactions, rebuild_sqlite
from mailquill.config import load_config
from mailquill.bootstrap import bootstrap_rules
from mailquill.gmail_client import build_service, list_all_labels, gmail_after_query
from mailquill.rules import save_rules
from mailquill.pipeline import run_pipeline
from mailquill.ingest import ingest_paths
from mailquill.report import generate_report


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


def _progress(label: str):
    """回傳一個進度回呼，在同一行更新 'label done/total'，完成時換行。"""
    def cb(done: int, total: int) -> None:
        end = "\n" if total and done >= total else ""
        sys.stdout.write(f"\r{label} {done}/{total}{end}")
        sys.stdout.flush()
    return cb


def _add_date_args(p) -> None:
    """為會抓 Gmail 的指令加上時間範圍選項。"""
    p.add_argument("--since", default=None,
                   help="只看此日期(含)之後的信，格式 YYYY-MM-DD；預設為今年 1/1")
    p.add_argument("--all", action="store_true",
                   help="不限時間，掃描全部歷史信件")


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _resolve_since(args) -> str | None:
    """回傳要套用的起始日期字串；--all 時回 None（不過濾），否則預設今年 1/1。

    格式錯誤直接報錯退出，避免無聲退回成全歷史掃描。
    """
    if getattr(args, "all", False):
        return None
    since = args.since or f"{datetime.now().year}-01-01"
    if not _DATE_RE.match(since):
        raise SystemExit(f"--since 格式需為 YYYY-MM-DD（收到：{since}）")
    return since


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mailquill")
    sub = parser.add_subparsers(dest="command", required=True)

    p_rebuild = sub.add_parser(
        "rebuild", help="重新套用分類並由 CSV 重建 SQLite"
    )
    p_rebuild.add_argument("--csv", default="transactions.csv")
    p_rebuild.add_argument("--db", default="mailquill.db")
    p_rebuild.add_argument("--categories", default="categories.yaml")

    p_labels = sub.add_parser(
        "labels", help="列出 Gmail 內所有 Label 的確切名稱（除錯／設定用）"
    )
    p_labels.add_argument("--config", default="config.yaml")

    p_bootstrap = sub.add_parser(
        "bootstrap", help="掃描 Gmail Label 產生 rules.yaml 草稿供確認"
    )
    p_bootstrap.add_argument("--config", default="config.yaml")
    _add_date_args(p_bootstrap)

    p_run = sub.add_parser(
        "run", help="抓取財務信件、解析、分類並更新 CSV/SQLite"
    )
    p_run.add_argument("--config", default="config.yaml")
    _add_date_args(p_run)

    p_ingest = sub.add_parser(
        "ingest", help="從本地 PDF 檔/資料夾匯入帳單（如富邦需手動下載者）"
    )
    p_ingest.add_argument("paths", nargs="+", help="PDF 檔或含 PDF 的資料夾")
    p_ingest.add_argument("--config", default="config.yaml")
    p_ingest.add_argument("--bank", default=None,
                          help="指定銀行 parser（如 fubon）；不給則依內容自動判斷")

    p_report = sub.add_parser(
        "report", help="由 SQLite 產生自包含 HTML 報表"
    )
    p_report.add_argument("--config", default="config.yaml")
    p_report.add_argument("--out", default="report.html")

    args = parser.parse_args(argv)
    if args.command == "rebuild":
        print("rebuild: 重新分類並重建 SQLite 中…")
        n = rebuild(args.csv, args.db, args.categories)
        print(f"rebuilt {n} transactions -> {args.db}")
        return 0
    if args.command == "labels":
        cfg = load_config(args.config)
        service = build_service(cfg.credentials_path, cfg.token_path)
        print("讀取 Gmail Label 清單中…")
        names = list_all_labels(service)
        print(f"Gmail 共 {len(names)} 個 Label：")
        for n in names:
            print(f"  - {n}")
        return 0
    if args.command == "bootstrap":
        cfg = load_config(args.config)
        service = build_service(cfg.credentials_path, cfg.token_path)
        since = _resolve_since(args)
        rules, missing = bootstrap_rules(service, cfg.label_names(),
                                         query=gmail_after_query(since),
                                         on_progress=_progress("掃描寄件者"))
        save_rules(cfg.rules_path, rules)
        print(f"bootstrap: 範圍={since or '全部歷史'}，"
              f"找到 {len(rules.senders)} 個寄件網域 -> {cfg.rules_path}")
        if missing:
            print(f"找不到 Gmail Label（{len(missing)}）：{', '.join(missing)}")
        print("請檢查並編輯 rules.yaml 後再執行 run。")
        return 0
    if args.command == "run":
        cfg = load_config(args.config)
        service = build_service(cfg.credentials_path, cfg.token_path)
        imported_at = datetime.now().isoformat(timespec="seconds")
        since = _resolve_since(args)
        r = run_pipeline(service, cfg, imported_at, query=gmail_after_query(since),
                         on_progress=_progress("處理信件"))
        print(f"run: 範圍={since or '全部歷史'} fetched={r.fetched} "
              f"matched={r.matched} added={r.added} skipped={r.skipped}")
        if r.missing_labels:
            print(f"找不到 Gmail Label（{len(r.missing_labels)}）：{', '.join(r.missing_labels)}")
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
        if r.fetch_failures:
            print(f"抓取失敗（{len(r.fetch_failures)}，已跳過，重跑可補回）：")
            for item in r.fetch_failures:
                print(f"  - {item}")
        return 0
    if args.command == "ingest":
        cfg = load_config(args.config)
        imported_at = datetime.now().isoformat(timespec="seconds")
        r = ingest_paths(args.paths, cfg, imported_at, bank=args.bank,
                         on_progress=_progress("匯入 PDF"))
        print(f"ingest: files={r.files} added={r.added} skipped={r.skipped}")
        for line in r.parsed:
            print(f"  {line}")
        if r.needs_parser:
            print(f"無對應 parser（{len(r.needs_parser)}）：")
            for item in r.needs_parser:
                print(f"  - {item}")
        if r.unlock_failures:
            print(f"解密失敗（{len(r.unlock_failures)}）：")
            for item in r.unlock_failures:
                print(f"  - {item}")
        if r.parse_warnings:
            print(f"解析警告（{len(r.parse_warnings)}）：")
            for item in r.parse_warnings:
                print(f"  - {item}")
        return 0
    if args.command == "report":
        cfg = load_config(args.config)
        print("report: 由 SQLite 產生 HTML 報表中…")
        out = generate_report(cfg.db_path, args.out)
        print(f"report: 已產生 {out}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
