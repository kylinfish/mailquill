import io

import pikepdf
from reportlab.pdfgen import canvas

from mailquill.config import Config
from mailquill.rules import Rules
from mailquill.gmail_message import EmailMessage, Attachment
from mailquill.store import read_transactions
import mailquill.pipeline as pipeline


def _encrypted_pdf(lines, password):
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 750
    for ln in lines:
        c.drawString(72, y, ln)
        y -= 20
    c.save()
    buf.seek(0)
    src = pikepdf.open(buf)
    enc = io.BytesIO()
    src.save(enc, encryption=pikepdf.Encryption(owner=password, user=password))
    return enc.getvalue()


def _cfg(tmp_path):
    # 寫一份只含一條分類規則的 categories.yaml
    cats = tmp_path / "categories.yaml"
    cats.write_text('rules:\n  - {keyword: "PXMART", l1: "食", l2: "生活採買"}\n',
                    encoding="utf-8")
    pwd = tmp_path / "passwords.txt"
    pwd.write_text("PW1\n", encoding="utf-8")
    return Config(
        label="財務",
        csv_path=str(tmp_path / "t.csv"),
        db_path=str(tmp_path / "t.db"),
        categories_path=str(cats),
        rules_path=str(tmp_path / "rules.yaml"),
        passwords_path=str(pwd),
    )


def _setup_common(monkeypatch, tmp_path, messages, parser_for):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(pipeline, "load_rules",
                        lambda path: Rules(senders=["@example-bank.test", "@unknown.test"],
                                           subject_keywords=[]))
    monkeypatch.setattr(pipeline, "list_labels_messages",
                        lambda service, labels, query=None: (list(messages.keys()), []))
    monkeypatch.setattr(pipeline, "get_message_metadata",
                        lambda service, mid: (messages[mid].sender,
                                              messages[mid].subject, messages[mid].date))
    monkeypatch.setattr(pipeline, "extract_message",
                        lambda service, mid: messages[mid])
    monkeypatch.setattr(pipeline, "get_parser", parser_for)
    return cfg


def test_run_pipeline_parses_encrypted_pdf_and_writes_csv(monkeypatch, tmp_path):
    pdf = _encrypted_pdf(["2026-06-01 PXMART 1,200"], "PW1")
    msg = EmailMessage(
        msg_id="m1", sender="ebill@example-bank.test", subject="帳單", date="",
        body_text="", attachments=[Attachment("stmt.pdf", "application/pdf", pdf)],
    )
    from mailquill.parsers.example_bank import ExampleBankParser
    cfg = _setup_common(monkeypatch, tmp_path, {"m1": msg},
                        parser_for=lambda m: ExampleBankParser())

    result = pipeline.run_pipeline(service=object(), cfg=cfg,
                                   imported_at="2026-06-24T10:00:00")
    assert result.fetched == 1
    assert result.matched == 1
    assert result.added == 1
    assert result.needs_parser == []
    assert result.unlock_failures == []

    rows = read_transactions(cfg.csv_path)
    assert len(rows) == 1
    assert rows[0].merchant_norm == "PXMART"
    assert rows[0].amount == "1200"
    assert rows[0].category_l1 == "食"
    assert rows[0].source_type == "pdf"


def test_run_pipeline_records_needs_parser(monkeypatch, tmp_path):
    msg = EmailMessage(msg_id="m1", sender="x@unknown.test", subject="帳單",
                       date="", body_text="", attachments=[])
    cfg = _setup_common(monkeypatch, tmp_path, {"m1": msg}, parser_for=lambda m: None)
    result = pipeline.run_pipeline(service=object(), cfg=cfg,
                                   imported_at="2026-06-24T10:00:00")
    assert result.matched == 1
    assert result.added == 0
    assert len(result.needs_parser) == 1
    assert "unknown.test" in result.needs_parser[0]


def test_run_pipeline_records_unlock_failure(monkeypatch, tmp_path):
    pdf = _encrypted_pdf(["2026-06-01 PXMART 1,200"], "RIGHT")  # 清單裡只有 PW1
    msg = EmailMessage(
        msg_id="m1", sender="ebill@example-bank.test", subject="帳單", date="",
        body_text="", attachments=[Attachment("stmt.pdf", "application/pdf", pdf)],
    )
    from mailquill.parsers.example_bank import ExampleBankParser
    cfg = _setup_common(monkeypatch, tmp_path, {"m1": msg},
                        parser_for=lambda m: ExampleBankParser())
    result = pipeline.run_pipeline(service=object(), cfg=cfg,
                                   imported_at="2026-06-24T10:00:00")
    assert len(result.unlock_failures) == 1
    assert result.added == 0  # PDF 解不開、body 也無交易


def test_run_pipeline_email_body_source_type(monkeypatch, tmp_path):
    from mailquill.parsers.example_bank import ExampleBankParser
    msg = EmailMessage(msg_id="m1", sender="ebill@example-bank.test", subject="帳單",
                       date="", body_text="2026-06-01 PXMART 1,200", attachments=[])
    cfg = _setup_common(monkeypatch, tmp_path, {"m1": msg},
                        parser_for=lambda m: ExampleBankParser())
    result = pipeline.run_pipeline(service=object(), cfg=cfg,
                                   imported_at="2026-06-24T10:00:00")
    assert result.added == 1
    rows = read_transactions(cfg.csv_path)
    assert rows[0].source_type == "email_body"


def test_run_pipeline_records_parse_warning_and_keeps_row(monkeypatch, tmp_path):
    from mailquill.raw_txn import RawTxn
    from mailquill.parsers.base import Parser

    class _NoAmountParser(Parser):
        bank = "B"
        def matches(self, msg):
            return True
        def parse(self, msg, pdf_texts):
            return [RawTxn(bank="B", date="2026-06-01", amount="無金額",
                           merchant_raw="商家X")]

    msg = EmailMessage(msg_id="m1", sender="ebill@example-bank.test", subject="帳單",
                       date="", body_text="", attachments=[])
    cfg = _setup_common(monkeypatch, tmp_path, {"m1": msg},
                        parser_for=lambda m: _NoAmountParser())
    result = pipeline.run_pipeline(service=object(), cfg=cfg,
                                   imported_at="2026-06-24T10:00:00")
    assert len(result.parse_warnings) == 1
    assert result.added == 1  # 警告不丟資料：原始列仍寫入
    rows = read_transactions(cfg.csv_path)
    assert rows[0].amount == ""  # 解不出金額存空字串，原始 merchant 保留
    assert rows[0].merchant_raw == "商家X"


def test_run_pipeline_reports_missing_labels(monkeypatch, tmp_path):
    from mailquill.parsers.example_bank import ExampleBankParser
    msg = EmailMessage(msg_id="m1", sender="ebill@example-bank.test", subject="帳單",
                       date="", body_text="2026-06-01 PXMART 1,200", attachments=[])
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(pipeline, "load_rules",
                        lambda path: Rules(senders=["@example-bank.test"], subject_keywords=[]))
    # 一個 label 找到（回傳 m1），另一個找不到
    monkeypatch.setattr(pipeline, "list_labels_messages",
                        lambda service, labels, query=None: (["m1"], ["不存在的標籤"]))
    monkeypatch.setattr(pipeline, "get_message_metadata",
                        lambda service, mid: (msg.sender, msg.subject, msg.date))
    monkeypatch.setattr(pipeline, "extract_message", lambda service, mid: msg)
    monkeypatch.setattr(pipeline, "get_parser", lambda m: ExampleBankParser())

    result = pipeline.run_pipeline(service=object(), cfg=cfg,
                                   imported_at="2026-06-24T10:00:00")
    assert result.missing_labels == ["不存在的標籤"]
    assert result.added == 1  # 找得到的 label 照常處理


def test_run_pipeline_threads_query(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    captured = {}

    def fake_list(service, labels, query=None):
        captured["query"] = query
        return [], []

    monkeypatch.setattr(pipeline, "load_rules",
                        lambda path: Rules(senders=[], subject_keywords=[]))
    monkeypatch.setattr(pipeline, "list_labels_messages", fake_list)
    pipeline.run_pipeline(service=object(), cfg=cfg,
                          imported_at="2026-06-24T10:00:00", query="after:2026/01/01")
    assert captured["query"] == "after:2026/01/01"


def test_run_pipeline_reports_progress(monkeypatch, tmp_path):
    from mailquill.parsers.example_bank import ExampleBankParser
    msgs = {
        "m1": EmailMessage(msg_id="m1", sender="ebill@example-bank.test", subject="帳單",
                           date="", body_text="2026-06-01 PXMART 1,200", attachments=[]),
        "m2": EmailMessage(msg_id="m2", sender="ebill@example-bank.test", subject="帳單",
                           date="", body_text="2026-06-02 PXMART 50", attachments=[]),
    }
    cfg = _setup_common(monkeypatch, tmp_path, msgs,
                        parser_for=lambda m: ExampleBankParser())
    seen = []
    pipeline.run_pipeline(service=object(), cfg=cfg, imported_at="2026-06-24T10:00:00",
                          on_progress=lambda done, total: seen.append((done, total)))
    assert seen == [(1, 2), (2, 2)]


def test_run_pipeline_skips_fetch_failure_and_continues(monkeypatch, tmp_path):
    from mailquill.parsers.example_bank import ExampleBankParser
    good = EmailMessage("ok", "ebill@example-bank.test", "帳單", "",
                        "2026-06-01 PXMART 1,200", [])
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(pipeline, "load_rules",
                        lambda p: Rules(senders=["@example-bank.test"], subject_keywords=[]))
    monkeypatch.setattr(pipeline, "list_labels_messages",
                        lambda s, l, query=None: (["bad", "ok"], []))

    def meta(s, mid):
        if mid == "bad":
            raise TimeoutError("read timed out")
        return (good.sender, good.subject, good.date)

    monkeypatch.setattr(pipeline, "get_message_metadata", meta)
    monkeypatch.setattr(pipeline, "extract_message", lambda s, mid: good)
    monkeypatch.setattr(pipeline, "get_parser", lambda m: ExampleBankParser())

    r = pipeline.run_pipeline(object(), cfg, "2026-06-24T10:00:00")
    assert r.added == 1                      # 壞的一封被跳過，好的照常寫入
    assert len(r.fetch_failures) == 1
    assert "bad" in r.fetch_failures[0]


def test_run_pipeline_skips_extract_failure(monkeypatch, tmp_path):
    from mailquill.parsers.example_bank import ExampleBankParser
    msg = EmailMessage("m1", "ebill@example-bank.test", "帳單", "", "", [])
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(pipeline, "load_rules",
                        lambda p: Rules(senders=["@example-bank.test"], subject_keywords=[]))
    monkeypatch.setattr(pipeline, "list_labels_messages",
                        lambda s, l, query=None: (["m1"], []))
    monkeypatch.setattr(pipeline, "get_message_metadata",
                        lambda s, mid: (msg.sender, msg.subject, msg.date))

    def boom(s, mid):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(pipeline, "extract_message", boom)
    monkeypatch.setattr(pipeline, "get_parser", lambda m: ExampleBankParser())

    r = pipeline.run_pipeline(object(), cfg, "2026-06-24T10:00:00")
    assert r.matched == 1
    assert r.added == 0
    assert len(r.fetch_failures) == 1 and "fetch" in r.fetch_failures[0]
