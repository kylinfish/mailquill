import textwrap
from dataclasses import replace

from mailquill.schema import Transaction
from mailquill.categorizer import load_categories, categorize, apply_categories, Rule


def _write_rules(tmp_path):
    p = tmp_path / "categories.yaml"
    p.write_text(textwrap.dedent("""
        rules:
          - keyword: "全聯"
            l1: "食"
            l2: "生活採買"
          - keyword: "中華電信"
            l1: "住"
            l2: "通訊"
    """), encoding="utf-8")
    return str(p)


def _txn(merchant):
    return Transaction(
        txn_id="x", date="2026-06-01", post_date="", amount="100.00",
        currency="TWD", merchant_raw=merchant, merchant_norm="",
        category_l1="未分類", category_l2="", bank="Cathay",
        account_last4="1234", source_type="email_body",
        source_msg_id="m1", raw_ref="", imported_at="2026-06-24T00:00:00",
    )


def test_load_categories(tmp_path):
    rules = load_categories(_write_rules(tmp_path))
    assert rules[0] == Rule(keyword="全聯", l1="食", l2="生活採買")
    assert len(rules) == 2


def test_categorize_match_substring(tmp_path):
    rules = load_categories(_write_rules(tmp_path))
    assert categorize("全聯福利中心 信義店", rules) == ("食", "生活採買")


def test_categorize_no_match_returns_uncategorized(tmp_path):
    rules = load_categories(_write_rules(tmp_path))
    assert categorize("某不知名商家", rules) == ("未分類", "")


def test_apply_categories_returns_updated_copy(tmp_path):
    rules = load_categories(_write_rules(tmp_path))
    txn = _txn("中華電信")
    out = apply_categories(txn, rules)
    assert (out.category_l1, out.category_l2) == ("住", "通訊")
    assert out.merchant_raw == "中華電信"
    assert txn.category_l1 == "未分類"  # 原物件不被改動


def test_categorize_is_fullwidth_and_case_insensitive(tmp_path):
    import textwrap
    p = tmp_path / "c.yaml"
    p.write_text(textwrap.dedent("""
        rules:
          - {keyword: "yoxi", l1: "行", l2: "計程車"}
          - {keyword: "GlobalMall", l1: "其他", l2: "百貨"}
          - {keyword: "Uber", l1: "行", l2: "計程車"}
    """), encoding="utf-8")
    rules = load_categories(str(p))
    assert categorize("ｙｏｘｉ", rules) == ("行", "計程車")          # 全形→半形
    assert categorize("ＧｌｏｂａｌＭａｌｌ", rules) == ("其他", "百貨")  # 全形→半形
    assert categorize("UBER EATS", rules) == ("行", "計程車")        # 大小寫
