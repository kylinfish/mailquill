"""國泰世華 parser 測試（完全合成資料，非真實帳單）。"""
from mailquill.gmail_message import EmailMessage
from mailquill.parsers import get_parser
from mailquill.parsers.cathay import CathayParser

# 模仿真實月結單 pdfplumber 抽出的版型（商家/金額/卡號皆為假資料）
_STATEMENT = """\
信用卡帳單 115年6月
測試人 先生
您本月消費明細如下：
消費日 交易說明 新臺幣金額 卡號 國家 幣別
上期帳單總額 30,914
06/10 06/10 本行自動扣繳 -30,914
02/21 05/25 保險費分期 03/12 1,770 9999 TW TWD
05/23 05/26 某計程車 95 9999 TW TWD
05/24 05/27 某加油站 1,125 9999 8888 TW TWD
06/01 06/03 某海外服務 945 9999 SG TWD 945.00 06/02
06/01 06/23 國外交易手續費 -海外 14 9999
06/01 06/03 點數折抵 -283 9999 TW TWD
12/28 01/05 跨年消費測試 500 9999 TW TWD
"""


def _msg(sender="ebill <statement@pxbillrc01.cathaybk.com.tw>"):
    return EmailMessage(msg_id="m1", sender=sender, subject="信用卡電子帳單",
                        date="", body_text="", attachments=[])


def test_cathay_parser_matches_sender():
    assert isinstance(get_parser(_msg()), CathayParser)
    assert get_parser(_msg("a@example-bank.test")) is None or not isinstance(
        get_parser(_msg("a@example-bank.test")), CathayParser)


def test_cathay_parses_consumption_lines_only():
    txns = CathayParser().parse(_msg(), [_STATEMENT])
    # 略過繳款、上期帳單總額；其餘 7 筆消費
    assert len(txns) == 7
    descs = [t.merchant_raw for t in txns]
    assert "本行自動扣繳" not in " ".join(descs)


def test_cathay_roc_year_and_fields():
    txns = CathayParser().parse(_msg(), [_STATEMENT])
    by_desc = {t.merchant_raw: t for t in txns}

    ins = by_desc["保險費分期 03/12"]
    assert ins.date == "2026-02-21"        # 民國115→2026
    assert ins.post_date == "2026-05-25"
    assert ins.amount == "1,770"
    assert ins.account_last4 == "9999"
    assert ins.bank == "Cathay"
    assert ins.currency == "TWD"

    assert by_desc["某計程車"].amount == "95"

    assert by_desc["某海外服務"].amount == "945"   # 取新臺幣金額，非外幣 945.00

    fee = by_desc["國外交易手續費 -海外"]
    assert fee.amount == "14"

    assert by_desc["點數折抵"].amount == "-283"    # 負數＝退款/折抵


def test_cathay_year_rollover():
    txns = CathayParser().parse(_msg(), [_STATEMENT])
    by_desc = {t.merchant_raw: t for t in txns}
    # 12 月消費出現在 6 月帳單 → 視為前一年；入帳日 01/05 仍屬帳單年
    assert by_desc["跨年消費測試"].date == "2025-12-28"
    assert by_desc["跨年消費測試"].post_date == "2026-01-05"


def test_cathay_no_period_header_returns_empty():
    no_header = "05/23 05/26 某計程車 95 9999 TW TWD\n"
    assert CathayParser().parse(_msg(), [no_header]) == []
