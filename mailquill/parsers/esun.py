"""玉山銀行（E.SUN Bank）信用卡電子帳單 parser。

明細欄序（每頁表頭）：
    消費日 入帳日 消費明細 [消費地] [外幣折算日] 幣別 金額 繳款幣別 金額 [行動支付]
例：  02/27 03/02 Some Shop USA Mountain View 02/27 TWD 33 TWD 33
      03/13 03/13 某分期商店 分06期之第06期 TWD 12,000 TWD 2,000   ← 分期取後者(本期繳款)
      02/28 03/02 國外交易服務費 TWD 2                             ← 單一金額

特點：
- 兩個日期是「消費日 入帳日」皆為 MM/DD，年份由帳單民國全日期（如 115/03/30 繳款截止日）補齊；
  取帳單內最大的民國全日期為參考年月，消費月大於參考月者視為前一年（分期原始消費會跨年）。
- 新臺幣金額 = 明細行「最後一個 TWD 金額」（即繳款金額；分期行首金額為原始總額，取後者）。
- 卡號末四碼在「卡號：5242-XXXX-XXXX-4285（…正卡）」標題行，跨行帶入每筆。
- 繳款行（「感謝您辦理本行自動轉帳繳款！」）只有單一日期，不符兩日期格式而略過。
"""
from __future__ import annotations

import re

from mailquill.gmail_message import EmailMessage
from mailquill.raw_txn import RawTxn
from mailquill.parsers.base import Parser
from mailquill.parsers._util import roc_to_ad, md_to_date

_ROC_FULL = re.compile(r"(\d{3})/(\d{2})/(\d{2})")           # 民國全日期 115/03/30
_PERIOD = re.compile(r"(\d{3})\s*年\s*(\d{1,2})\s*月")       # 備援：帳單期別 115年02月
_TXN = re.compile(r"^(\d{2})/(\d{2})\s+(\d{2})/(\d{2})\s+(.+)$")
_CARD = re.compile(r"卡號[:：]\s*[\dXx]{4}-[\dXx]{4}-[\dXx]{4}-(\d{4})")
_CUR = re.compile(r"[A-Z]{3}\s+-?[\d,]+")                    # 任一「幣別 金額」欄
_TWD = re.compile(r"TWD\s+(-?[\d,]+)")                        # 新臺幣金額
_FX_DATE_TAIL = re.compile(r"\s*\d{2}/\d{2}\s*$")            # 商家尾端的外幣折算日


class EsunParser(Parser):
    bank = "ESun"

    def matches(self, msg: EmailMessage) -> bool:
        return "esunbank.com.tw" in msg.sender

    def parse(self, msg: EmailMessage, pdf_texts: list[str]) -> list[RawTxn]:
        blocks = [msg.body_text] + list(pdf_texts)
        full = "\n".join(blocks)

        # 參考年/月：取帳單內最大的民國全日期（通常為繳款截止日，最能代表帳單週期末）
        rocs = [(roc_to_ad(int(y)), int(mm), int(dd))
                for y, mm, dd in _ROC_FULL.findall(full)]
        if rocs:
            ref = max(rocs)
            stmt_year, stmt_month = ref[0], ref[1]
        else:                                    # 備援：用帳單期別「115年02月」
            per = _PERIOD.search(full)
            if not per:
                return []
            stmt_year, stmt_month = roc_to_ad(int(per.group(1))), int(per.group(2))

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
                cmm, cdd, pmm, pdd, rest = m.groups()
                twd = _TWD.findall(rest)
                if not twd:                       # 無金額（分期附註、續行等）→ 略過
                    continue
                amount = twd[-1]                  # 最後一個 TWD = 本期繳款金額
                cur = _CUR.search(rest)           # 商家 = 第一個幣別欄之前
                merchant = rest[:cur.start()].strip() if cur else rest.strip()
                merchant = _FX_DATE_TAIL.sub("", merchant).strip()  # 去尾端外幣折算日
                if not merchant:
                    continue
                txns.append(RawTxn(
                    bank=self.bank,
                    date=md_to_date(cmm, cdd, stmt_year, stmt_month),       # 消費日
                    post_date=md_to_date(pmm, pdd, stmt_year, stmt_month),  # 入帳日
                    amount=amount,
                    merchant_raw=merchant,
                    account_last4=last4,
                    currency="TWD",
                ))
        return txns
