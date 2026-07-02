"""parser 共用小工具：民國年/日期轉換。"""
from __future__ import annotations


def roc_to_ad(roc_year: int) -> int:
    """民國年 → 西元年（115 → 2026）。"""
    return roc_year + 1911


def md_to_date(mm: str, dd: str, stmt_year: int, stmt_month: int) -> str:
    """MM/DD（無年份）+ 帳單年/月 → YYYY-MM-DD；月份大於帳單月視為前一年（跨年）。"""
    year = stmt_year - 1 if int(mm) > stmt_month else stmt_year
    return f"{year}-{mm}-{dd}"


def roc_date_to_iso(roc_year: str, mm: str, dd: str) -> str:
    """完整民國日期（115/05/25）→ 西元 ISO（2026-05-25）。"""
    return f"{roc_to_ad(int(roc_year))}-{mm}-{dd}"
