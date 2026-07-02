from mailquill.rules import Rules, load_rules, save_rules, matches


def test_save_then_load_roundtrip(tmp_path):
    path = str(tmp_path / "rules.yaml")
    rules = Rules(senders=["@cathaybk.com.tw"], subject_keywords=["帳單", "消費"])
    save_rules(path, rules)
    loaded = load_rules(path)
    assert loaded == rules


def test_load_missing_file_returns_empty(tmp_path):
    assert load_rules(str(tmp_path / "none.yaml")) == Rules([], [])


def test_matches_by_sender():
    rules = Rules(senders=["@cathaybk.com.tw"], subject_keywords=[])
    assert matches(rules, "信用卡 <ebill@cathaybk.com.tw>", "任意主旨") is True
    assert matches(rules, "noise <a@other.com>", "任意主旨") is False


def test_matches_by_subject_keyword():
    rules = Rules(senders=[], subject_keywords=["帳單"])
    assert matches(rules, "a@other.com", "您的電子帳單已產生") is True
    assert matches(rules, "a@other.com", "促銷通知") is False


def test_matches_requires_some_rule():
    rules = Rules(senders=[], subject_keywords=[])
    assert matches(rules, "a@b.com", "任何主旨") is False
