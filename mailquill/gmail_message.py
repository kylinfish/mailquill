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
    plain_texts: list[str] = []
    html_texts: list[str] = []
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
            plain_texts.append(decode_b64url(body["data"]).decode("utf-8", errors="replace"))
        elif mime == "text/html" and body.get("data"):
            html_texts.append(decode_b64url(body["data"]).decode("utf-8", errors="replace"))

    recurse(payload)
    texts = plain_texts if plain_texts else html_texts
    return texts, attachments
