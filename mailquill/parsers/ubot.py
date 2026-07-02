"""聯邦銀行（Union Bank of Taiwan, UBOT）信用卡電子帳單 parser。

明細欄序：入帳日 消費日 消費明細 [消費地] [結匯日 幣別 外幣金額] 新臺幣金額
例：  05/21 05/18 SomeShop Inc US 05/18 USD 20.00 620
      05/26 05/23 某餐廳 TW 178

特點（與國泰不同）：
- 兩個日期是「入帳日 消費日」(順序相反)，皆為 MM/DD，年份由帳單期別補。
- 卡號末四碼在「卡別標題行」(如「某信用卡 －正卡 9999」)，而非每筆明細。
- 新臺幣金額是明細行最後一個數字；其前可能有消費地/結匯日/幣別/外幣金額等欄位。
- 金額可為負（現金回饋、退款）。沒有兩個日期開頭的行(上期金額/總計/付款)會略過。
"""
from __future__ import annotations

import re

from mailquill.gmail_message import EmailMessage
from mailquill.raw_txn import RawTxn
from mailquill.parsers.base import Parser
from mailquill.parsers._util import roc_to_ad, md_to_date

_MONTH = re.compile(r"(\d{1,2})\s*月份")
_ROC_FULL = re.compile(r"(\d{3})/\d{2}/\d{2}")
_TXN = re.compile(r"^(\d{2})/(\d{2})\s+(\d{2})/(\d{2})\s+(.+)$")
_CARD = re.compile(r"(?:正卡|附卡)\D*(\d{4})\s*$")  # 卡別標題行尾的末四碼（錨定正卡/附卡，避免誤抓彙總行的數字）
_AMOUNT = re.compile(r"-?[\d,]+")
# 明細行尾可剝除的中繼欄位
_META = (
    re.compile(r"\d+\.\d{2}"),      # 外幣金額
    re.compile(r"[A-Z]{3}"),        # 幣別
    re.compile(r"\d{2}/\d{2}"),     # 結匯日
    re.compile(r"[A-Z]{2}"),        # 消費地/國別
)


class UnionBankParser(Parser):
    bank = "UnionBank"

    def matches(self, msg: EmailMessage) -> bool:
        return "ubot.com.tw" in msg.sender

    def parse(self, msg: EmailMessage, pdf_texts: list[str]) -> list[RawTxn]:
        blocks = [msg.body_text] + list(pdf_texts)
        full = "\n".join(blocks)
        mon, roc = _MONTH.search(full), _ROC_FULL.search(full)
        if not mon or not roc:
            return []
        stmt_month = int(mon.group(1))
        stmt_year = roc_to_ad(int(roc.group(1)))

        txns: list[RawTxn] = []
        last4 = ""
        for block in blocks:
            for line in block.splitlines():
                s = line.strip()
                m = _TXN.match(s)
                if not m:
                    card = _CARD.search(s)
                    if card:
                        last4 = card.group(1)
                    continue
                pmm, pdd, tmm, tdd, rest = m.groups()
                tokens = rest.split()
                if not tokens or not _AMOUNT.fullmatch(tokens[-1]):
                    continue
                amount = tokens[-1]
                meta = tokens[:-1]
                while meta and any(rx.fullmatch(meta[-1]) for rx in _META):
                    meta.pop()
                merchant = " ".join(meta).strip()
                if not merchant:
                    continue
                txns.append(RawTxn(
                    bank=self.bank,
                    date=md_to_date(tmm, tdd, stmt_year, stmt_month),       # 消費日
                    post_date=md_to_date(pmm, pdd, stmt_year, stmt_month),  # 入帳日
                    amount=amount,
                    merchant_raw=merchant,
                    account_last4=last4,
                    currency="TWD",
                ))
        return txns
