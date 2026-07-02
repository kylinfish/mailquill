import io

import pikepdf

from mailquill.pdf_unlocker import unlock_pdf, PdfUnlockResult


def _plain_pdf() -> bytes:
    pdf = pikepdf.new()
    pdf.add_blank_page()
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _encrypted_pdf(password: str) -> bytes:
    pdf = pikepdf.new()
    pdf.add_blank_page()
    buf = io.BytesIO()
    pdf.save(buf, encryption=pikepdf.Encryption(owner=password, user=password))
    return buf.getvalue()


def test_plain_pdf_returns_ok_without_password():
    res = unlock_pdf(_plain_pdf(), ["whatever"])
    assert res.ok is True
    assert res.password_used is None
    assert res.data is not None
    # 解出的 bytes 仍可被開啟
    with pikepdf.open(io.BytesIO(res.data)) as p:
        assert len(p.pages) == 1


def test_encrypted_pdf_unlocks_with_correct_password():
    res = unlock_pdf(_encrypted_pdf("SECRET1"), ["WRONG", "SECRET1"])
    assert res.ok is True
    assert res.password_used == "SECRET1"
    with pikepdf.open(io.BytesIO(res.data)) as p:  # 已解密，免密碼可開
        assert len(p.pages) == 1


def test_encrypted_pdf_all_passwords_fail():
    res = unlock_pdf(_encrypted_pdf("SECRET1"), ["NOPE", "ALSO_NO"])
    assert res.ok is False
    assert res.data is None
    assert res.error


def test_empty_password_list_on_encrypted_fails():
    res = unlock_pdf(_encrypted_pdf("SECRET1"), [])
    assert res.ok is False
    assert res.error
