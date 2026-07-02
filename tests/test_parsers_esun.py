"""玉山銀行 parser 測試（完全合成資料，非真實帳單）。"""
from mailquill.gmail_message import EmailMessage
from mailquill.parsers import get_parser
from mailquill.parsers.esun import EsunParser

# 模仿玉山月結單 pdfplumber 抽出的版型（商家/金額/卡號皆為假資料）
_STATEMENT = """\
這是您 115年02月 信用卡帳單
繳款截止日 115/03/30
消費日 入帳日 消費明細 消費地 外幣折算日 幣別 金額 繳款幣別 金額 行動支付
上期應繳金額： TWD 3,000
03/06 感謝您辦理本行自動轉帳繳款！ TWD -3,000
本期消費明細：
卡號：1234-XXXX-XXXX-9999（某信用卡－正卡）
09/10 03/13 某分期商店 分03期之第03期 TWD 9,000 TWD 3,000
09/10 03/13 (未到期金額0元 應付總費用年百分率0%)
02/27 03/02 某海外遊戲 USA Mountain View 02/27 TWD 100 TWD 100
02/27 03/02 國外交易服務費 TWD 2
03/05 03/06 某超市 TWD 500 TWD 500
本期合計： TWD 3,602
"""


def _msg(sender="ESUN <estatement@service.esunbank.com.tw>"):
    return EmailMessage("m1", sender, "信用卡電子帳單", "", "", [])


def test_esun_parser_matches():
    assert isinstance(get_parser(_msg()), EsunParser)


def test_esun_parses_consumption_only_and_sum_ties():
    txns = EsunParser().parse(_msg(), [_STATEMENT])
    # 略過繳款行(-3,000)與分期附註行；其餘 4 筆消費
    assert len(txns) == 4
    assert all(t.account_last4 == "9999" for t in txns)   # 來自卡號標題行
    assert all(t.bank == "ESun" for t in txns)
    total = sum(int(t.amount.replace(",", "")) for t in txns)
    assert total == 3602    # 3,000 + 100 + 2 + 500，對得起「本期合計」


def test_esun_installment_takes_current_period_amount_and_rollover():
    by = {t.merchant_raw: t for t in EsunParser().parse(_msg(), [_STATEMENT])}
    ins = by["某分期商店 分03期之第03期"]
    assert ins.amount == "3,000"          # 取後者(本期繳款)，非原始總額 9,000
    assert ins.date == "2025-09-10"       # 消費月(9) > 參考月(3) → 前一年
    assert ins.post_date == "2026-03-13"  # 入帳日仍屬帳單年


def test_esun_strips_location_and_fx_date_but_keeps_merchant():
    by = {t.merchant_raw: t for t in EsunParser().parse(_msg(), [_STATEMENT])}
    fx = by["某海外遊戲 USA Mountain View"]   # 已剝除尾端外幣折算日 02/27
    assert fx.amount == "100"                 # 取最後一個 TWD 金額
    assert fx.date == "2026-02-27"
    assert by["國外交易服務費"].amount == "2"  # 單一金額的手續費也計入


def test_esun_payment_line_skipped():
    descs = [t.merchant_raw for t in EsunParser().parse(_msg(), [_STATEMENT])]
    assert not any("感謝" in d for d in descs)   # 自動轉帳繳款不計為消費


def test_esun_no_period_returns_empty():
    # 無民國全日期、也無帳單期別 → 無法定年份 → 空清單（防呆）
    line = "02/27 03/02 某超市 TWD 500 TWD 500\n"
    assert EsunParser().parse(_msg(), [line]) == []
