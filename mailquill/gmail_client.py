"""Gmail API 服務函式。純解析委派給 gmail_message。"""
from __future__ import annotations

import os
import socket
import time

from mailquill.gmail_message import (
    Attachment, EmailMessage, decode_b64url, header_value, walk_payload,
)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# 暫時性錯誤：逾時/連線中斷/伺服器忙碌 → 退避重試（避免單封逾時就讓整批 run 中斷）
_RETRYABLE = (TimeoutError, socket.timeout, ConnectionError, OSError)
_RETRY_STATUSES = {429, 500, 502, 503, 504}


def _execute(request, retries: int = 4, sleep=time.sleep):
    """執行 Gmail API 請求；遇暫時性錯誤以指數退避重試，重試用盡才拋出。"""
    from googleapiclient.errors import HttpError

    for attempt in range(retries):
        try:
            return request.execute()
        except HttpError as e:
            status = getattr(getattr(e, "resp", None), "status", None)
            if status in _RETRY_STATUSES and attempt < retries - 1:
                sleep(min(2 ** attempt, 8))
                continue
            raise
        except _RETRYABLE:
            if attempt < retries - 1:
                sleep(min(2 ** attempt, 8))
                continue
            raise


def build_service(credentials_path: str, token_path: str):
    """OAuth 授權並回傳 Gmail service。整合用，不寫單元測試。"""
    import httplib2
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google_auth_httplib2 import AuthorizedHttp
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
    # 每個請求設 30 秒逾時，避免單一讀取無限期卡住；暫時性錯誤由 _execute 重試
    authed = AuthorizedHttp(creds, http=httplib2.Http(timeout=30))
    return build("gmail", "v1", http=authed, cache_discovery=False)


def label_id(service, label_name: str, user_id: str = "me") -> str:
    resp = _execute(service.users().labels().list(userId=user_id))
    for lab in resp.get("labels", []):
        if lab.get("name") == label_name:
            return lab["id"]
    raise ValueError(f"找不到 Gmail label: {label_name}")


def list_label_messages(service, label_name: str, query: str | None = None,
                        user_id: str = "me") -> list[str]:
    lid = label_id(service, label_name, user_id)
    return _list_messages_for_label_id(service, lid, query, user_id)


def _list_messages_for_label_id(service, lid: str, query: str | None,
                                user_id: str) -> list[str]:
    ids: list[str] = []
    page_token = None
    while True:
        resp = _execute(service.users().messages().list(
            userId=user_id, labelIds=[lid], q=query, pageToken=page_token,
        ))
        ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return ids


def gmail_after_query(since: str | None) -> str | None:
    """把 'YYYY-MM-DD' 轉成 Gmail 查詢 'after:YYYY/MM/DD'；since 為 None/空字串回 None。"""
    if not since:
        return None
    return "after:" + since.replace("-", "/")


def list_all_labels(service, user_id: str = "me") -> list[str]:
    """回傳 Gmail 內所有 Label 的名稱（排序）。除錯／設定用。"""
    resp = _execute(service.users().labels().list(userId=user_id))
    return sorted(lab.get("name", "") for lab in resp.get("labels", []))


def resolve_label_ids(service, label_names: list[str],
                      user_id: str = "me") -> tuple[list[str], list[str]]:
    """一次列出所有 label，回傳 (找到的 label id, 找不到的 label 名稱)。不拋例外。"""
    resp = _execute(service.users().labels().list(userId=user_id))
    name_to_id = {lab.get("name"): lab["id"] for lab in resp.get("labels", [])}
    found: list[str] = []
    missing: list[str] = []
    for name in label_names:
        if name in name_to_id:
            found.append(name_to_id[name])
        else:
            missing.append(name)
    return found, missing


def list_labels_messages(service, label_names: list[str], query: str | None = None,
                         user_id: str = "me") -> tuple[list[str], list[str]]:
    """聯集多個 label 的 message id（去重、保留首見順序）。

    回傳 (message id 清單, 找不到的 label 名稱清單)。找不到的 label 不會中斷，
    其餘 label 照常處理。
    """
    label_ids, missing = resolve_label_ids(service, label_names, user_id)
    seen: set[str] = set()
    ids: list[str] = []
    for lid in label_ids:
        for mid in _list_messages_for_label_id(service, lid, query, user_id):
            if mid not in seen:
                seen.add(mid)
                ids.append(mid)
    return ids, missing


def get_message_metadata(service, msg_id: str,
                         user_id: str = "me") -> tuple[str, str, str]:
    """只取 From/Subject/Date 標頭（format=metadata），不下載內文或附件。

    回傳 (sender, subject, date)。給 bootstrap 抽寄件者與 run 預先過濾用，
    避免為了讀標頭而下載整封信與 PDF 附件。
    """
    msg = _execute(service.users().messages().get(
        userId=user_id, id=msg_id, format="metadata",
        metadataHeaders=["From", "Subject", "Date"],
    ))
    headers = (msg.get("payload", {}) or {}).get("headers", []) or []
    return (
        header_value(headers, "From"),
        header_value(headers, "Subject"),
        header_value(headers, "Date"),
    )


def extract_message(service, msg_id: str, user_id: str = "me") -> EmailMessage:
    msg = _execute(service.users().messages().get(
        userId=user_id, id=msg_id, format="full",
    ))
    payload = msg.get("payload", {}) or {}
    headers = payload.get("headers", []) or []
    texts, att_specs = walk_payload(payload)

    attachments: list[Attachment] = []
    for spec in att_specs:
        if spec["inline_data"]:
            data = decode_b64url(spec["inline_data"])
        elif spec["attachment_id"]:
            resp = _execute(service.users().messages().attachments().get(
                userId=user_id, messageId=msg_id, id=spec["attachment_id"],
            ))
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
