from mailquill.gmail_message import EmailMessage
from mailquill.bootstrap import (
    sender_domain, aggregate_senders, bootstrap_rules, DEFAULT_SUBJECT_KEYWORDS,
)


def _msg(sender):
    return EmailMessage(msg_id="x", sender=sender, subject="", date="",
                        body_text="", attachments=[])


def test_sender_domain_extracts_domain():
    assert sender_domain("帳單 <ebill@cathaybk.com.tw>") == "@cathaybk.com.tw"
    assert sender_domain("noreply@CTBCbank.com") == "@ctbcbank.com"
    assert sender_domain("no-at-sign") == ""


def test_sender_domain_strips_trailing_comma():
    assert sender_domain("a@b.com, c@d.com") == "@b.com"


def test_sender_domain_prefers_angle_bracket_address():
    assert sender_domain('"weird@display" <real@bank.com>') == "@bank.com"


def test_aggregate_senders_dedups_in_order():
    msgs = [
        _msg("a <x@cathaybk.com.tw>"),
        _msg("b <y@ctbcbank.com>"),
        _msg("c <z@cathaybk.com.tw>"),
        _msg("bad-sender"),
    ]
    assert aggregate_senders(msgs) == ["@cathaybk.com.tw", "@ctbcbank.com"]


def test_bootstrap_rules_uses_fake_gmail(monkeypatch):
    import mailquill.bootstrap as b

    monkeypatch.setattr(b, "list_labels_messages",
                        lambda service, labels, query=None: (["m1", "m2"], []))
    senders = {
        "m1": "a <x@cathaybk.com.tw>",
        "m2": "b <y@ctbcbank.com>",
    }
    monkeypatch.setattr(b, "get_message_metadata",
                        lambda service, mid: (senders[mid], "", ""))

    rules, missing = bootstrap_rules(service=object(), label_names=["財務"])
    assert rules.senders == ["@cathaybk.com.tw", "@ctbcbank.com"]
    assert rules.subject_keywords == DEFAULT_SUBJECT_KEYWORDS
    assert missing == []


def test_bootstrap_rules_reports_missing_labels(monkeypatch):
    import mailquill.bootstrap as b

    monkeypatch.setattr(b, "list_labels_messages",
                        lambda service, labels, query=None: (["m1"], ["不存在的標籤"]))
    monkeypatch.setattr(b, "get_message_metadata",
                        lambda service, mid: ("a <x@cathaybk.com.tw>", "", ""))

    rules, missing = bootstrap_rules(service=object(), label_names=["財務", "不存在的標籤"])
    assert rules.senders == ["@cathaybk.com.tw"]
    assert missing == ["不存在的標籤"]


def test_bootstrap_rules_threads_query(monkeypatch):
    import mailquill.bootstrap as b
    captured = {}

    def fake_list(service, labels, query=None):
        captured["query"] = query
        return [], []

    monkeypatch.setattr(b, "list_labels_messages", fake_list)
    bootstrap_rules(service=object(), label_names=["財務"], query="after:2026/01/01")
    assert captured["query"] == "after:2026/01/01"


def test_bootstrap_rules_reports_progress(monkeypatch):
    import mailquill.bootstrap as b
    monkeypatch.setattr(b, "list_labels_messages",
                        lambda service, labels, query=None: (["m1", "m2", "m3"], []))
    monkeypatch.setattr(b, "get_message_metadata",
                        lambda service, mid: ("x@cathaybk.com.tw", "", ""))
    seen = []
    bootstrap_rules(service=object(), label_names=["財務"],
                    on_progress=lambda done, total: seen.append((done, total)))
    assert seen == [(1, 3), (2, 3), (3, 3)]
