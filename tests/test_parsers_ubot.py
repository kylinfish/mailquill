"""聯邦銀行 parser 測試（完全合成資料，非真實帳單）。"""
from mailquill.gmail_message import EmailMessage
from mailquill.parsers import get_parser
from mailquill.parsers.ubot import UnionBankParser

_STATEMENT = """\
親愛的測試卡友您好!
以下為您06月份之信用卡消費帳單：
115/06/27 已申請自動轉帳
入帳日 消費日 消費明細 結匯日 幣別 外幣金額 新臺幣金額
上期金額 842
上期付款金額已收到，謝謝！ -842
測試銀行卡 －正卡 9999
05/15 05/15 刷卡現金回饋－測試國內 -8
05/21 05/18 某海外服務 SG 05/18 TWD 560.00 560
05/21 05/18 國外交易手續費 560.00 TWD 8
05/26 05/23 某外送平台 TW 178
總計 738
"""


def _msg(sender="ebill <statement@ebillv2.card.ubot.com.tw>"):
    return EmailMessage("m1", sender, "信用卡帳單", "", "", [])


def test_ubot_parser_matches():
    assert isinstance(get_parser(_msg()), UnionBankParser)


def test_ubot_parses_only_dated_lines():
    txns = UnionBankParser().parse(_msg(), [_STATEMENT])
    assert len(txns) == 4   # 略過 上期金額/付款/總計
    assert all(t.account_last4 == "9999" for t in txns)   # 末四碼來自卡別標題行
    assert all(t.bank == "UnionBank" for t in txns)


def test_ubot_fields_and_date_order():
    by = {t.merchant_raw: t for t in UnionBankParser().parse(_msg(), [_STATEMENT])}
    nf = by["某海外服務"]                           # 已剝除 SG 05/18 TWD 560.00
    assert nf.date == "2026-05-18"                  # 消費日(第2欄)
    assert nf.post_date == "2026-05-21"             # 入帳日(第1欄)
    assert nf.amount == "560"                       # 取新臺幣金額(行末)
    assert by["國外交易手續費"].amount == "8"
    assert by["刷卡現金回饋－測試國內"].amount == "-8"   # 負數=回饋
    assert "某外送平台" in by


def test_ubot_sum_ties_total():
    txns = UnionBankParser().parse(_msg(), [_STATEMENT])
    total = sum(int(t.amount.replace(",", "")) for t in txns)
    assert total == 738    # -8 + 560 + 8 + 178


def test_ubot_no_period_returns_empty():
    assert UnionBankParser().parse(_msg(), ["05/21 05/18 某商店 560\n"]) == []
