"""本地 PDF 匯入測試。用 reportlab+pikepdf 產生加密的合成帳單。"""
import io

import pikepdf
from reportlab.pdfgen import canvas

from mailquill.config import Config
from mailquill.store import read_transactions
from mailquill.ingest import ingest_paths, collect_pdfs, select_parser


def _encrypted_pdf(lines, password):
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 750
    for ln in lines:
        c.drawString(72, y, ln)
        y -= 20
    c.save()
    buf.seek(0)
    src = pikepdf.open(buf)
    out = io.BytesIO()
    src.save(out, encryption=pikepdf.Encryption(owner=password, user=password))
    return out.getvalue()


def _cfg(tmp_path):
    cats = tmp_path / "categories.yaml"
    cats.write_text('rules:\n  - {keyword: "PXMART", l1: "食", l2: "生活採買"}\n',
                    encoding="utf-8")
    pwd = tmp_path / "passwords.txt"
    pwd.write_text("PW1\n", encoding="utf-8")
    return Config(
        label="財務",
        csv_path=str(tmp_path / "t.csv"),
        db_path=str(tmp_path / "t.db"),
        categories_path=str(cats),
        passwords_path=str(pwd),
    )


def test_collect_pdfs_file_and_dir(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"x")
    (tmp_path / "b.PDF").write_bytes(b"x")
    (tmp_path / "c.txt").write_bytes(b"x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "d.pdf").write_bytes(b"x")
    found = collect_pdfs([str(tmp_path)])
    assert sum(p.lower().endswith(".pdf") for p in found) == 3
    assert all(not p.endswith(".txt") for p in found)


def test_select_parser_picks_by_content():
    # example-bank 格式：YYYY-MM-DD 商家 金額
    parser, txns = select_parser("2026-06-01 PXMART 1,200\n2026-06-02 UBER 250\n")
    assert parser is not None
    assert len(txns) == 2


def test_ingest_processes_encrypted_pdf(tmp_path):
    pdf = _encrypted_pdf(["2026-06-01 PXMART 1,200"], "PW1")
    f = tmp_path / "stmt.pdf"
    f.write_bytes(pdf)
    cfg = _cfg(tmp_path)
    r = ingest_paths([str(f)], cfg, "2026-06-25T10:00:00")
    assert r.files == 1
    assert r.added == 1
    assert len(r.parsed) == 1
    rows = read_transactions(cfg.csv_path)
    assert rows[0].merchant_norm == "PXMART"
    assert rows[0].category_l1 == "食"
    assert rows[0].source_type == "pdf"


def test_ingest_dedups_on_reingest(tmp_path):
    pdf = _encrypted_pdf(["2026-06-01 PXMART 1,200"], "PW1")
    f = tmp_path / "stmt.pdf"
    f.write_bytes(pdf)
    cfg = _cfg(tmp_path)
    ingest_paths([str(f)], cfg, "2026-06-25T10:00:00")
    r2 = ingest_paths([str(f)], cfg, "2026-06-25T11:00:00")   # 再匯入一次
    assert r2.added == 0 and r2.skipped == 1                  # 去重，不重複入帳
    assert len(read_transactions(cfg.csv_path)) == 1


def test_ingest_unlock_failure(tmp_path):
    pdf = _encrypted_pdf(["2026-06-01 PXMART 1,200"], "WRONG")  # passwords.txt 只有 PW1
    f = tmp_path / "stmt.pdf"
    f.write_bytes(pdf)
    cfg = _cfg(tmp_path)
    r = ingest_paths([str(f)], cfg, "2026-06-25T10:00:00")
    assert r.added == 0
    assert len(r.unlock_failures) == 1


def test_ingest_needs_parser(tmp_path):
    pdf = _encrypted_pdf(["NOT A BANK STATEMENT - no parseable lines here"], "PW1")
    f = tmp_path / "stmt.pdf"
    f.write_bytes(pdf)
    cfg = _cfg(tmp_path)
    r = ingest_paths([str(f)], cfg, "2026-06-25T10:00:00")
    assert r.added == 0
    assert r.needs_parser == ["stmt.pdf"]
