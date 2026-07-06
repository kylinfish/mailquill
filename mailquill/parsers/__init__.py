"""Parser registry。各家 parser 在此註冊。"""
from __future__ import annotations

from mailquill.gmail_message import EmailMessage
from mailquill.parsers.base import Parser

_PARSERS: list[Parser] = []


def register(parser: Parser) -> None:
    _PARSERS.append(parser)


def get_parser(msg: EmailMessage) -> Parser | None:
    for p in _PARSERS:
        if p.matches(msg):
            return p
    return None


def all_parsers() -> list[Parser]:
    return list(_PARSERS)


# 註冊內建 parser（真實銀行 parser 在取得樣本後逐一加入）
from mailquill.parsers.example_bank import ExampleBankParser  # noqa: E402
from mailquill.parsers.cathay import CathayParser  # noqa: E402
from mailquill.parsers.ubot import UnionBankParser  # noqa: E402
from mailquill.parsers.taishin import TaishinParser  # noqa: E402
from mailquill.parsers.fubon import FubonParser  # noqa: E402
from mailquill.parsers.esun import EsunParser  # noqa: E402
from mailquill.parsers.sinopac import SinoPacParser  # noqa: E402

register(CathayParser())
register(UnionBankParser())
register(TaishinParser())
register(FubonParser())
register(EsunParser())
register(SinoPacParser())
register(ExampleBankParser())
