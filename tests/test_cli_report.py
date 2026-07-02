import mailquill.cli as cli
from mailquill.config import Config


def test_main_report_invokes_generate_and_returns_zero(monkeypatch, tmp_path, capsys):
    cfg = Config(label="財務", db_path=str(tmp_path / "t.db"))
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)

    captured = {}

    def fake_generate(db_path, out):
        captured["db_path"] = db_path
        captured["out"] = out
        return out

    monkeypatch.setattr(cli, "generate_report", fake_generate)

    out_path = str(tmp_path / "r.html")
    rc = cli.main(["report", "--config", "x.yaml", "--out", out_path])
    assert rc == 0
    assert captured["db_path"] == cfg.db_path
    assert captured["out"] == out_path
    assert out_path in capsys.readouterr().out
