"""台北富邦 parser 測試（完全合成資料，非真實帳單）。"""
from mailquill.gmail_message import EmailMessage
from mailquill.parsers import get_parser
from mailquill.parsers.fubon import FubonParser

_STATEMENT = """\
帳單年月 信用額度 帳單結帳日 繳款截止日
115/02 340,000 115/02/24 115/03/12
消費日期 消費說明 入帳日期 外幣折算日/幣別 外幣金額/消費地 台幣金額
前期應繳總額 10,084
115/02/10 自動扣繳 115/02/11 -10,084
115/02/24 悠遊卡轉置餘額 115/02/24 -85
JCB晶緻正卡末４碼9999
115/01/22 某商店股份有限公司 115/01/26 TWD 70
115/01/23 某外送平台 115/01/27 TWD 379
115/01/26 某咖啡店 建國門市 115/01/29 TWD 45
115/02/13 掛失費 123456******7890 200
本期應繳金額 8,325
"""


def _msg(sender="富邦 <ebill@cf.taipeifubon.com.tw>"):
    return EmailMessage("m1", sender, "信用卡帳單", "", "", [])


def test_fubon_parser_matches():
    assert isinstance(get_parser(_msg()), FubonParser)


def test_fubon_skips_precard_and_summary_rows():
    txns = FubonParser().parse(_msg(), [_STATEMENT])
    # 卡別標題行之後的 4 筆消費；略過 自動扣繳/悠遊卡轉置(卡別前)、前期/本期應繳
    assert len(txns) == 4
    descs = [t.merchant_raw for t in txns]
    assert "自動扣繳" not in " ".join(descs)
    assert "悠遊卡轉置餘額" not in " ".join(descs)


def test_fubon_fields_and_dates():
    by = {t.merchant_raw: t for t in FubonParser().parse(_msg(), [_STATEMENT])}

    a = by["某商店股份有限公司"]
    assert a.date == "2026-01-22"            # 消費日期(行首)
    assert a.post_date == "2026-01-26"       # 入帳日期(描述後)
    assert a.amount == "70"
    assert a.account_last4 == "9999"         # 來自「正卡末４碼9999」
    assert a.bank == "Fubon"

    fp = by["某外送平台"]
    assert fp.amount == "379"

    # 掛失費：無入帳日，需剝除遮罩卡號 123456******7890
    fee = by["掛失費"]
    assert fee.amount == "200"
    assert fee.post_date == ""


def test_fubon_no_card_header_returns_empty():
    # 沒有卡別標題行 → 不計任何消費（避免把繳款調整當消費）
    no_card = "115/01/22 某商店 115/01/26 TWD 70\n"
    assert FubonParser().parse(_msg(), [no_card]) == []
