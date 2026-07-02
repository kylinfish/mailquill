"""台新銀行 parser 測試（完全合成資料，非真實帳單）。"""
from mailquill.gmail_message import EmailMessage
from mailquill.parsers import get_parser
from mailquill.parsers.taishin import TaishinParser

_STATEMENT = """\
115年 06月 信用卡電子帳單
帳單結帳日 115/06/17
本期新增款項 4,000
消費日 入帳起息日消費明細 新臺幣金額 外幣折算日 消費地 幣別 外幣金額
測試聯名卡 (卡號末四碼:9999)
115/05/25 115/05/26 某商店TAIPEI 1,234 TW
115/05/25 115/05/26 某餐廳TAIPEI 2,766 TW
"""


def _msg(sender="台新銀行 <ebill@bhurecv.taishinbank.com.tw>"):
    return EmailMessage("m1", sender, "信用卡電子帳單", "", "", [])


def test_taishin_parser_matches():
    assert isinstance(get_parser(_msg()), TaishinParser)
    assert isinstance(get_parser(_msg("Richart <x@richart.tw>")), TaishinParser)


def test_taishin_parses_full_roc_dates_and_fields():
    txns = TaishinParser().parse(_msg(), [_STATEMENT])
    assert len(txns) == 2
    t = txns[0]
    assert t.date == "2026-05-25"            # 完整民國日期轉西元
    assert t.post_date == "2026-05-26"
    assert t.amount == "1,234"               # 描述後第一個純數字（非消費地 TW）
    assert t.merchant_raw == "某商店TAIPEI"
    assert t.account_last4 == "9999"         # 來自(卡號末四碼:9999)
    assert t.bank == "Taishin"
    assert t.currency == "TWD"


def test_taishin_sum_ties_total():
    txns = TaishinParser().parse(_msg(), [_STATEMENT])
    total = sum(int(t.amount.replace(",", "")) for t in txns)
    assert total == 4000
