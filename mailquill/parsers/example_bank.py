"""範例 parser（模板）。比對測試用網域，不會命中真實銀行信件。

新增一家銀行 = 複製本檔，改 bank、matches 的寄件者比對、parse 的版型規則，
並在 parsers/__init__.py 註冊一個實例。
"""
from __future__ import annotations

import re

from mailquill.gmail_message import EmailMessage
from mailquill.raw_txn import RawTxn
from mailquill.parsers.base import Parser

_LINE_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s+(.+?)\s+([\d,]+(?:\.\d+)?)\s*$")


class ExampleBankParser(Parser):
    bank = "ExampleBank"

    def matches(self, msg: EmailMessage) -> bool:
        return "@example-bank.test" in msg.sender

    def parse(self, msg: EmailMessage, pdf_texts: list[str]) -> list[RawTxn]:
        txns: list[RawTxn] = []
        blocks = [msg.body_text] + list(pdf_texts)
        for block in blocks:
            for line in block.splitlines():
                m = _LINE_RE.match(line)
                if m:
                    txns.append(RawTxn(
                        bank=self.bank,
                        date=m.group(1),
                        merchant_raw=m.group(2),
                        amount=m.group(3),
                    ))
        return txns
