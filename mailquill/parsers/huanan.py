"""華南銀行（Hua Nan Bank）信用卡電子帳單 parser。

只解析月結單消費明細（PDF，需先由 pdf_unlocker 解密）。明細行格式：
    交易日 入帳日 交易摘要 新臺幣金額 交易國家或地區 [外幣折算日 幣別 外幣金額]
例：  114/07/21 115/07/01 某保險公司 －分期本金 12/12 1,233 TW

消費行只出現在卡片區段（「卡名： 1234********5678」標頭）之後；摘要區的
繳款、上期結欠等行落在任何卡片區段之前，且多半缺少國別欄，自然被略過。
兩個例外仍須納入：
- 摘要區的「現金回饋/退貨」調整行（如「ｉ網購生活卡網路消費現金回饋 -15」）
  是真實金流；繳款行（含「繳款」字樣）仍排除。
- 卡片區段內的費用行（年費、優惠減免等）沒有國別欄，另以「日期 日期 摘要
  金額」比對；分期交易的「分期總金額NT$…」補充行無日期開頭，不會誤判。
date 記交易日、post_date 記入帳日（完整民國日期，直接轉西元）。
外幣交易取新臺幣金額欄（外幣明細格式依表頭推定，尚無真實樣本驗證）。
"""
from __future__ import annotations

import re

from mailquill.gmail_message import EmailMessage
from mailquill.raw_txn import RawTxn
from mailquill.parsers.base import Parser
from mailquill.parsers._util import roc_date_to_iso

# 帳單標頭：115年07 月份信用卡電子帳單
_HEADER_RE = re.compile(r"(\d{2,3})\s*年\s*(\d{1,2})\s*月份信用卡電子帳單")
# 卡片區段標頭：卡名： 1234********9999
_CARD_RE = re.compile(r"^.+?：\s*\d{4}\*+(\d{4})\s*$")
# 明細行：交易日 入帳日 摘要 金額 國別 [外幣折算日 幣別 外幣金額]
_TXN_RE = re.compile(
    r"^(\d{2,3})/(\d{2})/(\d{2})\s+(\d{2,3})/(\d{2})/(\d{2})\s+"
    r"(.+?)\s+(-?[\d,]+)\s+([A-Z]{2})"
    r"(?:\s+\d{2,3}/\d{2}/\d{2}\s+[A-Z]{3}\s+-?[\d,.]+)?\s*$"
)
# 無國別欄的調整/費用行：交易日 入帳日 摘要 金額
_ADJ_RE = re.compile(
    r"^(\d{2,3})/(\d{2})/(\d{2})\s+(\d{2,3})/(\d{2})/(\d{2})\s+(.+?)\s+(-?[\d,]+)\s*$"
)


class HuaNanParser(Parser):
    bank = "HuaNan"

    def matches(self, msg: EmailMessage) -> bool:
        return "hncb.com.tw" in msg.sender

    def parse(self, msg: EmailMessage, pdf_texts: list[str]) -> list[RawTxn]:
        txns: list[RawTxn] = []
        for block in [msg.body_text] + list(pdf_texts):
            if not _HEADER_RE.search(block):
                continue
            last4 = ""
            for line in block.splitlines():
                line = line.strip()
                card = _CARD_RE.match(line)
                if card:
                    last4 = card.group(1)
                    continue
                if not last4:
                    # 摘要區：只收「回饋/退貨」調整行（真實金流），繳款行排除
                    a = _ADJ_RE.match(line)
                    if a and re.search(r"回饋|退貨", a.group(7)) \
                            and "繳款" not in a.group(7):
                        ty, tm, td, py, pm, pd, merchant, amount = a.groups()
                        txns.append(RawTxn(
                            bank=self.bank,
                            date=roc_date_to_iso(ty, tm, td),
                            post_date=roc_date_to_iso(py, pm, pd),
                            amount=amount,
                            merchant_raw=merchant.strip(),
                            currency="TWD",
                        ))
                    continue
                m = _TXN_RE.match(line)
                if not m:
                    # 卡片區段內的費用行（年費、減免等）沒有國別欄
                    a = _ADJ_RE.match(line)
                    if not a:
                        continue
                    ty, tm, td, py, pm, pd, merchant, amount = a.groups()
                    txns.append(RawTxn(
                        bank=self.bank,
                        date=roc_date_to_iso(ty, tm, td),
                        post_date=roc_date_to_iso(py, pm, pd),
                        amount=amount,
                        merchant_raw=merchant.strip(),
                        account_last4=last4,
                        currency="TWD",
                    ))
                    continue
                ty, tm, td, py, pm, pd, merchant, amount, _country = m.groups()
                txns.append(RawTxn(
                    bank=self.bank,
                    date=roc_date_to_iso(ty, tm, td),
                    post_date=roc_date_to_iso(py, pm, pd),
                    amount=amount,
                    merchant_raw=merchant.strip(),
                    account_last4=last4,
                    currency="TWD",
                ))
        return txns
