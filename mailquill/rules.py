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
