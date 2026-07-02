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

    def get(self, userId, id, format=None, metadataHeaders=None):
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


from mailquill.gmail_client import resolve_label_ids, list_labels_messages


def test_resolve_label_ids_found_and_missing():
    svc = FakeService()
    svc.labels = [{"id": "L1", "name": "財務"}, {"id": "L2", "name": "銀行"}]
    found, missing = resolve_label_ids(svc, ["財務", "銀行", "不存在"])
    assert found == ["L1", "L2"]
    assert missing == ["不存在"]


class _LabelKeyedMessages:
    def __init__(self, by_label):
        self._by_label = by_label

    def list(self, userId, labelIds=None, q=None, pageToken=None):
        lid = labelIds[0]
        return _Exec({"messages": [{"id": m} for m in self._by_label.get(lid, [])]})


class _UsersLK:
    def __init__(self, fake):
        self._fake = fake

    def messages(self):
        return _LabelKeyedMessages(self._fake.by_label)

    def labels(self):
        return _Labels(self._fake)


class LabelKeyedService:
    def __init__(self, labels, by_label):
        self.labels = labels
        self.by_label = by_label

    def users(self):
        return _UsersLK(self)


def test_list_labels_messages_unions_dedups_and_reports_missing():
    svc = LabelKeyedService(
        labels=[{"id": "L1", "name": "財務"}, {"id": "L2", "name": "銀行"}],
        by_label={"L1": ["m1", "m2"], "L2": ["m2", "m3"]},  # m2 跨 label 重複
    )
    ids, missing = list_labels_messages(svc, ["財務", "銀行", "信用卡"])
    assert ids == ["m1", "m2", "m3"]   # 去重、保留首見順序
    assert missing == ["信用卡"]


def test_list_all_labels_returns_sorted_names():
    from mailquill.gmail_client import list_all_labels
    svc = FakeService()
    svc.labels = [{"id": "L2", "name": "銀行/台新銀行"},
                  {"id": "L1", "name": "財務"},
                  {"id": "L3", "name": "INBOX"}]
    assert list_all_labels(svc) == ["INBOX", "財務", "銀行/台新銀行"]


def test_gmail_after_query():
    from mailquill.gmail_client import gmail_after_query
    assert gmail_after_query("2026-01-01") == "after:2026/01/01"
    assert gmail_after_query(None) is None
    assert gmail_after_query("") is None


def test_get_message_metadata_reads_headers_only():
    from mailquill.gmail_client import get_message_metadata
    svc = FakeService()
    svc.messages = {
        "m1": {"payload": {"headers": [
            {"name": "From", "value": "帳單 <ebill@cathaybk.com.tw>"},
            {"name": "Subject", "value": "電子帳單"},
            {"name": "Date", "value": "Mon, 01 Jun 2026 10:00:00 +0800"},
        ]}}
    }
    sender, subject, date = get_message_metadata(svc, "m1")
    assert sender == "帳單 <ebill@cathaybk.com.tw>"
    assert subject == "電子帳單"
    assert date.startswith("Mon, 01 Jun 2026")


def test_execute_retries_then_succeeds():
    from mailquill.gmail_client import _execute

    class _Req:
        def __init__(self): self.n = 0
        def execute(self):
            self.n += 1
            if self.n < 3:
                raise TimeoutError("read timed out")
            return {"ok": True}

    r = _Req()
    assert _execute(r, retries=4, sleep=lambda _: None) == {"ok": True}
    assert r.n == 3   # 失敗兩次後第三次成功


def test_execute_raises_after_exhausting_retries():
    from mailquill.gmail_client import _execute

    class _Req:
        def execute(self): raise TimeoutError("always")

    with pytest.raises(TimeoutError):
        _execute(_Req(), retries=3, sleep=lambda _: None)
