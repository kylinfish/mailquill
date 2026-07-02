"""由既有 Gmail Label 聚合寄件網域，產生 rules 草稿。"""
from __future__ import annotations

import re

from mailquill.gmail_message import EmailMessage
from mailquill.gmail_client import list_labels_messages, get_message_metadata
from mailquill.rules import Rules

DEFAULT_SUBJECT_KEYWORDS = ["帳單", "消費", "交易", "明細", "刷卡", "扣款", "繳費"]

_ANGLE_RE = re.compile(r"<([^>]+)>")
_DOMAIN_RE = re.compile(r"@([^\s>,;\"']+)")


def sender_domain(sender: str) -> str:
    m = _ANGLE_RE.search(sender)
    candidate = m.group(1) if m else sender
    d = _DOMAIN_RE.search(candidate)
    if not d:
        return ""
    return "@" + d.group(1).lower()


def aggregate_sender_domains(senders: list[str]) -> list[str]:
    """由寄件者字串清單取出去重、保留首見順序的網域清單。"""
    seen: list[str] = []
    for sender in senders:
        dom = sender_domain(sender)
        if dom and dom not in seen:
            seen.append(dom)
    return seen


def aggregate_senders(messages: list[EmailMessage]) -> list[str]:
    return aggregate_sender_domains([msg.sender for msg in messages])


def bootstrap_rules(service, label_names: list[str],
                    query: str | None = None,
                    on_progress=None) -> tuple[Rules, list[str]]:
    """掃描多個 Label 聚合寄件網域，回傳 (Rules 草稿, 找不到的 Label 名稱)。

    query 為選用的 Gmail 搜尋字串（如 'after:2026/01/01'），用來限定時間範圍。
    """
    ids, missing = list_labels_messages(service, label_names, query=query)
    # 只取寄件者標頭，不下載內文／附件（bootstrap 只需要寄件網域）
    total = len(ids)
    senders: list[str] = []
    for i, mid in enumerate(ids):
        if on_progress:
            on_progress(i + 1, total)
        senders.append(get_message_metadata(service, mid)[0])
    rules = Rules(
        senders=aggregate_sender_domains(senders),
        subject_keywords=list(DEFAULT_SUBJECT_KEYWORDS),
    )
    return rules, missing
