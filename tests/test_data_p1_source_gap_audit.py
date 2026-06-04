import os
import pytest

AUDIT_DOC_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs", "DATA_SOURCE_GAP_AUDIT.md")


def _read_audit():
    with open(AUDIT_DOC_PATH, "r", encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("keyword", [
    "fund_flow",
    "lhb",
    "raw_evidence",
    "fallback",
    "live smoke",
    "融资融券",
    "研报",
    "评级",
    "回购",
    "DATA-P0-FUND-ROUTE",
    "source_catalog",
    "fixture",
])
def test_audit_doc_contains_key_concept(keyword):
    content = _read_audit()
    assert keyword in content, f"Audit document missing key concept: {keyword}"


def test_audit_doc_exists():
    assert os.path.isfile(AUDIT_DOC_PATH)


def test_audit_doc_has_gap_table():
    content = _read_audit()
    assert "差距 1" in content
    assert "差距 2" in content
    assert "差距 3" in content
    assert "差距 4" in content
    assert "差距 5" in content


def test_audit_doc_has_at_least_5_gaps():
    content = _read_audit()
    gap_count = content.count("差距 ")
    assert gap_count >= 5, f"Expected at least 5 gaps, found {gap_count}"


def test_audit_doc_has_task_suggestions():
    content = _read_audit()
    assert "DATA-010" in content
    assert "DATA-011" in content
    assert "下一轮任务建议" in content


def test_audit_doc_explains_fund_flow_failure():
    content = _read_audit()
    assert "失败字符串" in content
    assert "假成功" in content or "误判为成功" in content
    assert "ProxyError" in content or "ConnectionError" in content


def test_audit_doc_not_marking_unimplemented_as_done():
    content = _read_audit()
    assert "融资融券" in content
    lines = content.split("\n")
    margin_lines = [l for l in lines if "融资融券" in l and "❌" in l]
    assert len(margin_lines) >= 2, "融资融券 should have ❌ markers indicating unimplemented"


def test_audit_doc_has_data_type_matrix():
    content = _read_audit()
    assert "OHLCV" in content
    assert "个股资金流" in content or "资金流" in content
    assert "龙虎榜" in content
    assert "公告" in content
    assert "新闻" in content


def test_audit_doc_mentions_simon_comparison():
    content = _read_audit()
    assert "Simon" in content
    assert "端点目录" in content
    assert "fallback" in content.lower()
