"""華南銀行 parser 測試（完全合成資料，非真實帳單）。"""
from mailquill.gmail_message import EmailMessage
from mailquill.parsers import get_parser
from mailquill.parsers.huanan import HuaNanParser

# 模仿真實月結單 pdfplumber 抽出的版型（商家/金額/卡號皆為假資料）
_STATEMENT = """\
115年07 月份信用卡電子帳單 第 1 頁共 3頁
帳單結帳日 繳款截止日 信用額度 國內預借現金額度 國外預借現金額度 循環信用利率 利率適用期限
115/07/01 115/07/16 230,000 23,000 69,000 7.36% 115/07
上期應繳總額 - 繳款金額 - 調整/退貨金額 + 本期新增款項 = 本期應繳總額 本期最低應繳金額
10,000 10,000 0 12,345 12,345 1,000
交易日 入帳日 交易摘要 新臺幣金額 交易國家或地區 外幣折算日 幣別 外幣金額
115/07/01 上期應繳總額： 10,000
115/06/16 115/06/16 您上期繳款已入帳－華銀自動扣繳 -10,000
115/07/01 115/07/01 ｉ網購生活卡網路消費現金回饋 -15
115/07/01 上期溢繳款項小計 -15
115/07/01 上期結欠款項 0
------------------------------------------------------------------------------
測試聯名卡： 1234********9999
114/07/21 115/07/01 測試保險公司（股） －分期本金 12/12 1,233 TW
分期總金額NT$14,802、未到期金額NT$0、本期分期利息NT$0.0及應付總費用年百分率0.00%
115/06/29 115/07/01 測試商店 2,000 TW
115/06/20 115/06/22 測試海外服務 945 US 115/06/21 USD 30.00
115/06/25 115/06/26 測試退貨商店 -283 TW
115/07/01 115/07/01 恭喜您，已達年費優惠減免門檻 0
115/07/01 115/07/01 年費 2,400
＊＊＊消費小計＊＊＊ 6,295
------------------------------------------------------------------------------
測試網購卡： 4595********8888
115/05/01 115/06/04 １１５年房屋稅 1,253 TW
＊＊＊消費小計＊＊＊ 1,253
------------------------------------------------------------------------------
本期應繳總額： 12,345
結 束
"""


def _msg(sender="華南銀行 <service@ebmail.hncb.com.tw>"):
    return EmailMessage(msg_id="m1", sender=sender, subject="華南銀行信用卡電子帳單",
                        date="", body_text="", attachments=[])


def test_huanan_parser_matches_sender():
    assert isinstance(get_parser(_msg()), HuaNanParser)
    other = get_parser(_msg("a@example-bank.test"))
    assert other is None or not isinstance(other, HuaNanParser)


def test_huanan_parses_card_lines_and_summary_rebates():
    txns = HuaNanParser().parse(_msg(), [_STATEMENT])
    # 卡片區段內 5 筆消費＋2 筆費用行（年費/減免，無國別欄）＋摘要區現金回饋 1 筆
    assert len(txns) == 8
    descs = " ".join(t.merchant_raw for t in txns)
    assert "繳款已入帳" not in descs
    assert "上期應繳總額" not in descs
    by_desc = {t.merchant_raw: t for t in txns}
    rebate = by_desc["ｉ網購生活卡網路消費現金回饋"]
    assert rebate.amount == "-15"
    assert rebate.account_last4 == ""      # 摘要區行沒有卡號
    fee = by_desc["年費"]                   # 費用行沒有國別欄（TW）也要計入
    assert fee.amount == "2,400"
    assert fee.account_last4 == "9999"     # 屬於所在卡片區段


def test_huanan_roc_dates_and_fields():
    txns = HuaNanParser().parse(_msg(), [_STATEMENT])
    by_desc = {t.merchant_raw: t for t in txns}

    ins = by_desc["測試保險公司（股） －分期本金 12/12"]
    assert ins.date == "2025-07-21"        # 交易日（民國114→2025，分期原始刷卡日）
    assert ins.post_date == "2026-07-01"   # 入帳日（民國115→2026）
    assert ins.amount == "1,233"
    assert ins.account_last4 == "9999"
    assert ins.bank == "HuaNan"
    assert ins.currency == "TWD"

    assert by_desc["測試商店"].amount == "2,000"
    assert by_desc["測試商店"].date == "2026-06-29"
    assert by_desc["測試商店"].post_date == "2026-07-01"
    assert by_desc["測試海外服務"].post_date == "2026-06-22"
    assert by_desc["測試退貨商店"].amount == "-283"   # 負數＝退款/折抵

    # 第二張卡的交易帶自己的末四碼
    assert by_desc["１１５年房屋稅"].account_last4 == "8888"


def test_huanan_foreign_txn_takes_ntd_amount():
    txns = HuaNanParser().parse(_msg(), [_STATEMENT])
    by_desc = {t.merchant_raw: t for t in txns}
    # 取新臺幣金額 945，非外幣 30.00
    assert by_desc["測試海外服務"].amount == "945"
    assert by_desc["測試海外服務"].currency == "TWD"


def test_huanan_no_statement_header_returns_empty():
    no_header = "115/06/29 115/07/01 測試商店 2,000 TW\n"
    assert HuaNanParser().parse(_msg(), [no_header]) == []
