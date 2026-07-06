"""永豐銀行（SinoPac）parser 測試（完全合成資料，非真實帳單）。"""
from mailquill.gmail_message import EmailMessage
from mailquill.parsers import get_parser
from mailquill.parsers.sinopac import SinoPacParser

# 模仿永豐月結單 pdfplumber 抽出的版型（商家/金額/卡號皆為假資料）。
# 含：域內、海外(剝外幣尾)、國外交易服務費、被拆到相鄰行的海外商家、A- 前綴、繳款行。
_STATEMENT = """\
入帳 卡號 外幣 外幣 總費用 分期未到期
消費日 帳單說明 臺幣金額
起息日 末四碼 折算日 金額 年百分率 金額
06/02 06/02 永豐自扣已入帳，謝謝！ -3,000
05/06 05/22 9999 某產物保險股份有限公司 2,180
A- SOME SHOP-
05/15 05/19 9999 461 05/15 THB472.00
CITY TH
05/16 05/19 9999 A- SOME NOODLE BANGKOK TH 166 05/16 THB170.00
05/16 05/19 9999 SOME NOODLE 國外交易服務費 2
05/24 05/27 9999 A- 中油－某站（Ｄ５１４Ｌ） 166
"""


def _msg(sender="SinoPac <estatement@banksinopac.com.tw>",
         subject="永豐信用卡電子帳單 115年06月"):
    return EmailMessage("m1", sender, subject, "", "", [])


def test_sinopac_parser_matches():
    assert isinstance(get_parser(_msg()), SinoPacParser)


def test_sinopac_parses_consumption_only():
    txns = SinoPacParser().parse(_msg(), [_STATEMENT])
    # 略過繳款行(-3,000)與表頭；其餘 5 筆消費
    assert len(txns) == 5
    assert all(t.account_last4 == "9999" for t in txns)   # 卡號末四在每筆錨定行
    assert all(t.bank == "SinoPac" for t in txns)
    assert not any("永豐自扣" in t.merchant_raw for t in txns)   # 繳款不計為消費
    total = sum(int(t.amount.replace(",", "")) for t in txns)
    assert total == 2975    # 2,180 + 461 + 166 + 2 + 166


def test_sinopac_domestic_amount_and_year():
    by = {t.merchant_raw: t for t in SinoPacParser().parse(_msg(), [_STATEMENT])}
    t = by["某產物保險股份有限公司"]
    assert t.amount == "2,180"
    assert t.date == "2026-05-06"          # 115年 → 2026
    assert t.post_date == "2026-05-22"


def test_sinopac_foreign_strips_fx_tail():
    by = {t.merchant_raw: t for t in SinoPacParser().parse(_msg(), [_STATEMENT])}
    t = by["SOME NOODLE BANGKOK TH"]        # 已剝除尾端「05/16 THB170.00」
    assert t.amount == "166"                # 取臺幣金額，非外幣 170.00
    assert by["SOME NOODLE 國外交易服務費"].amount == "2"


def test_sinopac_recovers_wrapped_merchant():
    # 海外長商家名被拆到錨定行的前一行，仍能補回（而非空白）
    merchants = [t.merchant_raw for t in SinoPacParser().parse(_msg(), [_STATEMENT])]
    assert "SOME SHOP-" in merchants        # 來自前一行碎片
    assert all(m for m in merchants)        # 無空商家


def test_sinopac_strips_a_prefix():
    merchants = [t.merchant_raw for t in SinoPacParser().parse(_msg(), [_STATEMENT])]
    assert "中油－某站（Ｄ５１４Ｌ）" in merchants   # 「A- 」前綴已剝除
    assert not any(m.startswith("A-") for m in merchants)


def test_sinopac_no_period_returns_empty():
    # 無主旨/內文/PDF 期別 → 不臆測年份 → 空清單
    m = _msg(subject="永豐信用卡電子帳單")   # 主旨無期別
    assert SinoPacParser().parse(m, ["05/06 05/22 9999 某商店 100\n"]) == []
