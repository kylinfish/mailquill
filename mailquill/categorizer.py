"""兩層 rule-based 分類：關鍵字出現在商家字串內即命中。

比對前會先做 Unicode 正規化（NFKC）與大小寫摺疊（casefold），所以
全形/半形（ＱＴａｘｉ↔QTaxi、ＧｌｏｂａｌＭａｌｌ↔GlobalMall）與大小寫
（Uber↔UBER）都視為相同，台灣帳單常見的全形英數字也能正確命中。
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, replace

import yaml

from mailquill.schema import Transaction


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKC", s).casefold()


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
    folded = _fold(merchant_raw)
    for rule in rules:
        if _fold(rule.keyword) in folded:
            return (rule.l1, rule.l2)
    return ("未分類", "")


def apply_categories(txn: Transaction, rules: list[Rule]) -> Transaction:
    l1, l2 = categorize(txn.merchant_raw, rules)
    return replace(txn, category_l1=l1, category_l2=l2)
