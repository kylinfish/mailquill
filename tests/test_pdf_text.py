import io

from reportlab.pdfgen import canvas

from mailquill.pdf_text import extract_pdf_text


def _text_pdf(lines: list[str]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 750
    for line in lines:
        c.drawString(72, y, line)
        y -= 20
    c.save()
    return buf.getvalue()


def test_extract_pdf_text_reads_lines():
    pdf = _text_pdf(["2026-06-01 PXMART 1,200", "2026-06-02 UBER 250"])
    text = extract_pdf_text(pdf)
    assert "PXMART" in text
    assert "1,200" in text
    assert "UBER" in text


def test_extract_pdf_text_empty_pdf_returns_empty_string():
    import pikepdf
    pdf = pikepdf.new()
    pdf.add_blank_page()
    buf = io.BytesIO()
    pdf.save(buf)
    assert extract_pdf_text(buf.getvalue()) == ""
