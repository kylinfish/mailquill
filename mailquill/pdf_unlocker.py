"""用本地密碼清單解開加密 PDF。永不拋例外。"""
from __future__ import annotations

import io
from dataclasses import dataclass

import pikepdf


@dataclass
class PdfUnlockResult:
    ok: bool
    data: bytes | None
    password_used: str | None
    error: str | None


def _save_decrypted(pdf: pikepdf.Pdf) -> bytes:
    out = io.BytesIO()
    pdf.save(out)  # 不帶 encryption 參數 → 存成未加密
    return out.getvalue()


def unlock_pdf(pdf_bytes: bytes, passwords: list[str]) -> PdfUnlockResult:
    # 先嘗試無密碼開啟（未加密的情況）
    try:
        with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
            return PdfUnlockResult(True, _save_decrypted(pdf), None, None)
    except pikepdf.PasswordError:
        pass
    except Exception as e:  # 壞檔等
        return PdfUnlockResult(False, None, None, f"無法開啟 PDF: {e}")

    # 加密：逐一嘗試密碼
    for pw in passwords:
        try:
            with pikepdf.open(io.BytesIO(pdf_bytes), password=pw) as pdf:
                return PdfUnlockResult(True, _save_decrypted(pdf), pw, None)
        except pikepdf.PasswordError:
            continue
        except Exception as e:
            return PdfUnlockResult(False, None, None, f"解密時出錯: {e}")

    return PdfUnlockResult(False, None, None, "密碼清單皆無法解開此加密 PDF")
