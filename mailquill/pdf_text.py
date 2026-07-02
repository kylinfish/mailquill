"""用 pdfplumber 從 PDF bytes 抽出文字。"""
from __future__ import annotations

import io

import pdfplumber


def extract_pdf_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)
