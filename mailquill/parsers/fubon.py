"""台北富邦銀行（Taipei Fubon）信用卡電子帳單 parser。

明細欄序：消費日期 消費說明 入帳日期 [外幣折算日/幣別 外幣金額/消費地] 台幣金額
例：  115/01/22 某商店股份有限公司 115/01/26 TWD 350
      115/02/13 掛失費 123456******7890 200   （費用行，無入帳日）

特點：
- 兩個完整民國日期「不相鄰」：消費日期在行首，入帳日期夾在描述之後。
- 卡號末四碼在卡別標題行「…正卡末４碼9999」（含全形數字）。
- 台幣金額是行末數字。描述取「消費日期」之後、「入帳日期」之前的字串；
  若無入帳日(如掛失費)則剝除行末的遮罩卡號/幣別等。
- 只計卡別標題行之後的消費；卡別前的「自動扣繳/悠遊卡轉置」屬繳款調整，略過。
"""
from __future__ import annotations

import re

from mailquill.gmail_message import EmailMessage
from mailquill.raw_txn import RawTxn
from mailquill.parsers.base import Parser
from mailquill.parsers._util import roc_date_to_iso

_TXN_DATE = re.compile(r"^(\d{3})/(\d{2})/(\d{2})\s+(.+)$")
_ROC = re.compile(r"(\d{3})/(\d{2})/(\d{2})")
_CARD = re.compile(r"末.?碼\s*(\d{4})")          # 「正卡末４碼9999」
_AMOUNT = re.compile(r"-?[\d,]+")
_MASKED = re.compile(r"[\d*]+\*+[\d*]+")          # 遮罩卡號 123456******7890
_TAIL = re.compile(r"[A-Z]{2,3}|\d+\.\d{2}")      # 幣別/消費地/外幣金額


class FubonParser(Parser):
    bank = "Fubon"

    def matches(self, msg: EmailMessage) -> bool:
        return "taipeifubon.com.tw" in msg.sender

    def parse(self, msg: EmailMessage, pdf_texts: list[str]) -> list[RawTxn]:
        blocks = [msg.body_text] + list(pdf_texts)
        txns: list[RawTxn] = []
        last4 = ""
        for block in blocks:
            for line in block.splitlines():
                s = line.strip()
                m = _TXN_DATE.match(s)
                if not m:
                    card = _CARD.search(s)
                    if card:
                        last4 = card.group(1)
                    continue
                if not last4:        # 卡別標題行之前的繳款調整，略過
                    continue
                cy, cm, cd, rest = m.groups()
                tokens = rest.split()
                if not tokens or not _AMOUNT.fullmatch(tokens[-1]):
                    continue
                amount = tokens[-1]
                middle = tokens[:-1]

                post = ""
                desc_tokens = middle
                for i, t in enumerate(middle):
                    d = _ROC.fullmatch(t)
                    if d:
                        post = roc_date_to_iso(d.group(1), d.group(2), d.group(3))
                        desc_tokens = middle[:i]
                        break
                else:
                    while desc_tokens and (_MASKED.fullmatch(desc_tokens[-1])
                                           or _TAIL.fullmatch(desc_tokens[-1])):
                        desc_tokens = desc_tokens[:-1]

                merchant = " ".join(desc_tokens).strip()
                if not merchant:
                    continue
                txns.append(RawTxn(
                    bank=self.bank,
                    date=roc_date_to_iso(cy, cm, cd),       # 消費日期
                    post_date=post,                          # 入帳日期
                    amount=amount,
                    merchant_raw=merchant,
                    account_last4=last4,
                    currency="TWD",
                ))
        return txns
