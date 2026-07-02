import base64

from mailquill.gmail_message import (
    Attachment, EmailMessage, decode_b64url, header_value, walk_payload,
)


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


def test_decode_b64url_handles_missing_padding():
    assert decode_b64url(_b64("帳單明細")) == "帳單明細".encode("utf-8")


def test_header_value_case_insensitive():
    headers = [{"name": "From", "value": "a@b.com"}, {"name": "Subject", "value": "S"}]
    assert header_value(headers, "from") == "a@b.com"
    assert header_value(headers, "SUBJECT") == "S"
    assert header_value(headers, "Cc") == ""


def test_walk_payload_collects_text_and_attachments():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "multipart/alternative", "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("消費 1200 元")}},
                {"mimeType": "text/html", "body": {"data": _b64("<p>ignored</p>")}},
            ]},
            {"mimeType": "application/pdf", "filename": "stmt.pdf",
             "body": {"attachmentId": "att-1", "size": 999}},
        ],
    }
    texts, atts = walk_payload(payload)
    assert texts == ["消費 1200 元"]
    assert atts == [{
        "filename": "stmt.pdf", "mime_type": "application/pdf",
        "attachment_id": "att-1", "inline_data": None,
    }]


def test_walk_payload_inline_attachment_data():
    payload = {
        "mimeType": "application/pdf", "filename": "x.pdf",
        "body": {"data": _b64("PDFDATA")},
    }
    texts, atts = walk_payload(payload)
    assert texts == []
    assert atts[0]["inline_data"] == _b64("PDFDATA")
    assert atts[0]["attachment_id"] is None


def test_walk_payload_falls_back_to_html_when_no_plaintext():
    payload = {
        "mimeType": "text/html",
        "body": {"data": _b64("<h1>Hello</h1>")},
    }
    texts, atts = walk_payload(payload)
    assert texts == ["<h1>Hello</h1>"]
    assert atts == []


def test_walk_payload_plain_wins_over_html_when_both_present():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("plain text")}},
            {"mimeType": "text/html", "body": {"data": _b64("<p>html</p>")}},
        ],
    }
    texts, atts = walk_payload(payload)
    assert texts == ["plain text"]
    assert atts == []


def test_dataclasses_construct():
    msg = EmailMessage(
        msg_id="m1", sender="a@b.com", subject="S", date="D",
        body_text="B", attachments=[Attachment("f.pdf", "application/pdf", b"x")],
    )
    assert msg.attachments[0].data == b"x"
