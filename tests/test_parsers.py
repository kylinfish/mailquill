from mailquill.gmail_message import EmailMessage
from mailquill.raw_txn import RawTxn
from mailquill.parsers import get_parser, all_parsers, register
from mailquill.parsers.base import Parser
from mailquill.parsers.example_bank import ExampleBankParser


def _msg(sender, body=""):
    return EmailMessage(msg_id="m1", sender=sender, subject="帳單", date="",
                        body_text=body, attachments=[])


def test_example_parser_registered():
    assert any(isinstance(p, ExampleBankParser) for p in all_parsers())


def test_get_parser_matches_example_bank():
    msg = _msg("帳單 <ebill@example-bank.test>")
    p = get_parser(msg)
    assert isinstance(p, ExampleBankParser)


def test_get_parser_returns_none_for_unknown():
    assert get_parser(_msg("noreply <x@unknown.test>")) is None


def test_example_parser_parses_body_and_pdf_lines():
    msg = _msg("ebill@example-bank.test", body="2026-06-01 PXMART 1,200")
    txns = get_parser(msg).parse(msg, ["2026-06-02 UBER 250"])
    assert [(t.date, t.merchant_raw, t.amount) for t in txns] == [
        ("2026-06-01", "PXMART", "1,200"),
        ("2026-06-02", "UBER", "250"),
    ]
    assert all(t.bank == "ExampleBank" for t in txns)


def test_base_parser_raises_not_implemented():
    msg = _msg("a@b.test")
    base = Parser()
    try:
        base.matches(msg)
        assert False, "should raise"
    except NotImplementedError:
        pass


def test_register_adds_parser():
    before = len(all_parsers())

    class _Tmp(Parser):
        bank = "Tmp"
        def matches(self, msg):
            return False
        def parse(self, msg, pdf_texts):
            return []

    register(_Tmp())
    assert len(all_parsers()) == before + 1
