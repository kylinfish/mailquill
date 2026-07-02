"""Parser 基底類別。各家銀行繼承並實作 matches / parse。"""
from __future__ import annotations

from mailquill.gmail_message import EmailMessage
from mailquill.raw_txn import RawTxn


class Parser:
    bank: str = ""

    def matches(self, msg: EmailMessage) -> bool:
        raise NotImplementedError

    def parse(self, msg: EmailMessage, pdf_texts: list[str]) -> list[RawTxn]:
        """回傳此封信的原始交易。

        作者契約：`msg.body_text` 一定是 `str`（可能為空字串，不會是 None）；
        `pdf_texts` 是已解密 PDF 逐頁文字以換行串接後的字串清單；金額/日期可直接
        回傳原始字串，後續由 normalizer 清洗，無需在此正規化。
        """
        raise NotImplementedError
