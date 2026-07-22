"""Tests for api.services.feishu_export_service — B-003 研报导出为飞书文档."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_report(**overrides):
    defaults = dict(
        id="rpt-feishu-1",
        user_id="u1",
        symbol="600519",
        trade_date="2025-06-01",
        status="completed",
        decision="BUY",
        direction="看多",
        research_direction="看多",
        execution_action="ENTER",
        action_label="条件入场",
        confidence=85,
        target_price=1800.0,
        stop_loss_price=1650.0,
        market_report='Full market analysis...\n<!-- VERDICT: {"direction": "BULLISH", "reason": "均线多头排列，MACD金叉确认上涨趋势"} -->',
        sentiment_report='Full sentiment analysis...\n<!-- VERDICT: {"direction": "BULLISH", "reason": "社交媒体情绪积极，机构评级上调"} -->',
        news_report=None,
        fundamentals_report='Full fundamentals analysis...\n<!-- VERDICT: {"direction": "NEUTRAL", "reason": "营收增长但利润率承压"} -->',
        macro_report=None,
        smart_money_report=None,
        volume_price_report=None,
        game_theory_report=None,
        risk_items=[
            {"name": "政策风险", "level": "high", "description": "Regulatory changes"},
            {"name": "流动性风险", "level": "low", "description": "Normal liquidity"},
        ],
        key_metrics=[
            {"name": "PE(TTM)", "value": "35.2", "status": "neutral"},
            {"name": "营收增长", "value": "12.5%", "status": "good"},
        ],
        analyst_traces=None,
        investment_plan="分批建仓，首次30%",
        trader_investment_plan="开盘观察30分钟确认趋势后入场",
        final_trade_decision="Buy at open with 50% position.",
        result_data={
            "local_knowledge_block": "知识库补充：茅台估值处于历史中位",
            "half_year_facts_block": "半年报营收增长12%",
        },
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_minimal_report(**overrides):
    """Report with only required fields, all optional fields None."""
    defaults = dict(
        id="rpt-min",
        user_id="u1",
        symbol="000001",
        trade_date="2025-01-01",
        status="completed",
        decision=None,
        direction=None,
        research_direction=None,
        execution_action=None,
        action_label=None,
        confidence=None,
        target_price=None,
        stop_loss_price=None,
        market_report=None,
        sentiment_report=None,
        news_report=None,
        fundamentals_report=None,
        macro_report=None,
        smart_money_report=None,
        volume_price_report=None,
        game_theory_report=None,
        risk_items=None,
        key_metrics=None,
        analyst_traces=None,
        investment_plan=None,
        trader_investment_plan=None,
        final_trade_decision=None,
        result_data=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# is_feishu_configured
# ---------------------------------------------------------------------------

class TestIsFeishuConfigured:
    @patch.dict("os.environ", {"FEISHU_APP_ID": "cli_xxx", "FEISHU_APP_SECRET": "secret"})
    def test_true_when_both_set(self):
        from api.services.feishu_export_service import is_feishu_configured
        assert is_feishu_configured() is True

    @patch.dict("os.environ", {}, clear=True)
    def test_false_when_missing(self):
        from api.services.feishu_export_service import is_feishu_configured
        assert is_feishu_configured() is False

    @patch.dict("os.environ", {"FEISHU_APP_ID": "cli_xxx"}, clear=True)
    def test_false_when_only_id(self):
        from api.services.feishu_export_service import is_feishu_configured
        assert is_feishu_configured() is False


# ---------------------------------------------------------------------------
# render_report_markdown
# ---------------------------------------------------------------------------

class TestRenderReportMarkdown:
    def test_contains_symbol_and_date(self):
        from api.services.feishu_export_service import render_report_markdown
        md = render_report_markdown(_make_report())
        assert "600519" in md
        assert "2025-06-01" in md

    def test_contains_decision_info(self):
        from api.services.feishu_export_service import render_report_markdown
        md = render_report_markdown(_make_report())
        # action_label takes precedence over decision
        assert "条件入场" in md
        assert "看多" in md
        assert "85%" in md
        assert "1800" in md
        assert "1650" in md

    def test_uses_decision_when_no_action_label(self):
        from api.services.feishu_export_service import render_report_markdown
        md = render_report_markdown(_make_report(action_label=None))
        assert "BUY" in md

    def test_contains_action_label(self):
        from api.services.feishu_export_service import render_report_markdown
        md = render_report_markdown(_make_report())
        assert "条件入场" in md

    def test_contains_agent_verdicts(self):
        from api.services.feishu_export_service import render_report_markdown
        md = render_report_markdown(_make_report())
        assert "均线多头排列" in md
        assert "社交媒体情绪积极" in md
        assert "营收增长但利润率承压" in md

    def test_skips_none_sections(self):
        from api.services.feishu_export_service import render_report_markdown
        md = render_report_markdown(_make_report())
        # news_report is None, should not produce a verdict entry
        assert "新闻分析" not in md

    def test_contains_key_metrics(self):
        from api.services.feishu_export_service import render_report_markdown
        md = render_report_markdown(_make_report())
        assert "PE(TTM)" in md
        assert "35.2" in md
        assert "营收增长" in md

    def test_omits_key_metrics_when_empty(self):
        from api.services.feishu_export_service import render_report_markdown
        md = render_report_markdown(_make_report(key_metrics=None))
        assert "关键指标" not in md

    def test_contains_risk_items(self):
        from api.services.feishu_export_service import render_report_markdown
        md = render_report_markdown(_make_report())
        assert "政策风险" in md
        assert "流动性风险" in md
        assert "[高]" in md

    def test_contains_final_trade_decision(self):
        from api.services.feishu_export_service import render_report_markdown
        md = render_report_markdown(_make_report())
        assert "Buy at open with 50% position." in md

    def test_contains_investment_plan(self):
        from api.services.feishu_export_service import render_report_markdown
        md = render_report_markdown(_make_report())
        assert "分批建仓" in md

    def test_contains_trader_plan(self):
        from api.services.feishu_export_service import render_report_markdown
        md = render_report_markdown(_make_report())
        assert "开盘观察30分钟" in md

    def test_contains_local_knowledge_block(self):
        from api.services.feishu_export_service import render_report_markdown
        md = render_report_markdown(_make_report())
        assert "本地知识补充" in md
        assert "茅台估值处于历史中位" in md

    def test_contains_half_year_facts_block(self):
        from api.services.feishu_export_service import render_report_markdown
        md = render_report_markdown(_make_report())
        assert "半年报事实对照" in md
        assert "半年报营收增长12%" in md

    def test_minimal_report_no_crash(self):
        from api.services.feishu_export_service import render_report_markdown
        md = render_report_markdown(_make_minimal_report())
        assert "000001" in md
        assert "TradingAgents" in md

    def test_contains_footer(self):
        from api.services.feishu_export_service import render_report_markdown
        md = render_report_markdown(_make_report())
        assert "不构成投资建议" in md

    def test_verdict_alias_conversion(self):
        from api.services.feishu_export_service import render_report_markdown
        report = _make_report(
            market_report='Analysis\n<!-- VERDICT: {"direction": "LEAN_BEARISH", "reason": "趋势偏弱"} -->',
        )
        md = render_report_markdown(report)
        assert "偏空" in md
        assert "趋势偏弱" in md


# ---------------------------------------------------------------------------
# markdown_to_blocks
# ---------------------------------------------------------------------------

class TestMarkdownToBlocks:
    def test_heading_blocks(self):
        from api.services.feishu_export_service import markdown_to_blocks
        blocks = markdown_to_blocks("# Title\n## Subtitle\n### H3")
        assert len(blocks) == 3
        assert blocks[0]["block_type"] == 3  # heading1
        assert blocks[1]["block_type"] == 4  # heading2
        assert blocks[2]["block_type"] == 5  # heading3

    def test_bullet_blocks(self):
        from api.services.feishu_export_service import markdown_to_blocks
        blocks = markdown_to_blocks("- item one\n- item two")
        assert len(blocks) == 2
        assert blocks[0]["block_type"] == 12  # bullet
        assert blocks[1]["block_type"] == 12

    def test_ordered_blocks(self):
        from api.services.feishu_export_service import markdown_to_blocks
        blocks = markdown_to_blocks("1. first\n2. second")
        assert len(blocks) == 2
        assert blocks[0]["block_type"] == 13  # ordered

    def test_divider_block(self):
        from api.services.feishu_export_service import markdown_to_blocks
        blocks = markdown_to_blocks("text\n---\nmore")
        assert any(b["block_type"] == 22 for b in blocks)

    def test_paragraph_block(self):
        from api.services.feishu_export_service import markdown_to_blocks
        blocks = markdown_to_blocks("Hello world")
        assert len(blocks) == 1
        assert blocks[0]["block_type"] == 2
        elements = blocks[0]["text"]["elements"]
        assert elements[0]["text_run"]["content"] == "Hello world"

    def test_bold_inline(self):
        from api.services.feishu_export_service import markdown_to_blocks
        blocks = markdown_to_blocks("This is **bold** text")
        elements = blocks[0]["text"]["elements"]
        assert len(elements) == 3
        assert elements[1]["text_run"]["content"] == "bold"
        assert elements[1]["text_run"]["text_element_style"]["bold"] is True

    def test_table_as_paragraphs(self):
        from api.services.feishu_export_service import markdown_to_blocks
        md = "| A | B |\n| --- | --- |\n| 1 | 2 |"
        blocks = markdown_to_blocks(md)
        # Separator row should be skipped
        assert len(blocks) == 2
        assert "A" in blocks[0]["text"]["elements"][0]["text_run"]["content"]

    def test_empty_input(self):
        from api.services.feishu_export_service import markdown_to_blocks
        assert markdown_to_blocks("") == []

    def test_skip_separator_row(self):
        from api.services.feishu_export_service import markdown_to_blocks
        blocks = markdown_to_blocks("| --- | --- |")
        assert len(blocks) == 0


# ---------------------------------------------------------------------------
# export_report_to_feishu — config check
# ---------------------------------------------------------------------------

class TestExportConfigCheck:
    @patch.dict("os.environ", {}, clear=True)
    def test_returns_error_when_not_configured(self):
        from api.services.feishu_export_service import export_report_to_feishu
        result = export_report_to_feishu(_make_report())
        assert result["success"] is False
        assert "未配置" in result["error"]


# ---------------------------------------------------------------------------
# export_report_to_feishu — happy path
# ---------------------------------------------------------------------------

class TestExportHappyPath:
    @patch.dict("os.environ", {
        "FEISHU_APP_ID": "cli_test",
        "FEISHU_APP_SECRET": "secret_test",
    })
    @patch("api.services.feishu_export_service._batch_create_blocks")
    @patch("api.services.feishu_export_service._get_document_blocks")
    @patch("api.services.feishu_export_service._create_document")
    @patch("api.services.feishu_export_service._fetch_tenant_access_token")
    def test_success_returns_url(
        self, mock_token, mock_create, mock_get_blocks, mock_batch
    ):
        from api.services.feishu_export_service import export_report_to_feishu

        mock_token.return_value = "t-fake-token"
        mock_create.return_value = {
            "document_id": "doc_abc123",
            "url": "https://feishu.cn/docx/doc_abc123",
        }
        mock_get_blocks.return_value = [{"block_id": "page_block"}]
        mock_batch.return_value = None

        result = export_report_to_feishu(_make_report())

        assert result["success"] is True
        assert result["document_url"] == "https://feishu.cn/docx/doc_abc123"
        assert result["document_token"] == "doc_abc123"
        assert "600519" in result["title"]
        mock_create.assert_called_once()
        mock_batch.assert_called()

    @patch.dict("os.environ", {
        "FEISHU_APP_ID": "cli_test",
        "FEISHU_APP_SECRET": "secret_test",
        "FEISHU_FOLDER_TOKEN": "fld_xyz",
    })
    @patch("api.services.feishu_export_service._batch_create_blocks")
    @patch("api.services.feishu_export_service._get_document_blocks")
    @patch("api.services.feishu_export_service._create_document")
    @patch("api.services.feishu_export_service._fetch_tenant_access_token")
    def test_passes_folder_token(
        self, mock_token, mock_create, mock_get_blocks, mock_batch
    ):
        from api.services.feishu_export_service import export_report_to_feishu

        mock_token.return_value = "t-fake-token"
        mock_create.return_value = {
            "document_id": "doc_abc",
            "url": "https://feishu.cn/docx/doc_abc",
        }
        mock_get_blocks.return_value = [{"block_id": "page"}]
        mock_batch.return_value = None

        export_report_to_feishu(_make_report())

        call_kwargs = mock_create.call_args
        assert call_kwargs[0][1] == "fld_xyz"  # folder_token arg


# ---------------------------------------------------------------------------
# export_report_to_feishu — error handling
# ---------------------------------------------------------------------------

class TestExportErrorHandling:
    @patch.dict("os.environ", {
        "FEISHU_APP_ID": "cli_test",
        "FEISHU_APP_SECRET": "secret_test",
    })
    @patch("api.services.feishu_export_service._create_document")
    @patch("api.services.feishu_export_service._fetch_tenant_access_token")
    def test_returns_error_on_request_exception(self, mock_token, mock_create):
        import requests as _req
        from api.services.feishu_export_service import export_report_to_feishu

        mock_token.return_value = "t-fake-token"
        mock_create.side_effect = _req.ConnectionError("Connection refused")

        result = export_report_to_feishu(_make_report())
        assert result["success"] is False
        assert "请求失败" in result["error"]

    @patch.dict("os.environ", {
        "FEISHU_APP_ID": "cli_test",
        "FEISHU_APP_SECRET": "secret_test",
    })
    @patch("api.services.feishu_export_service._create_document")
    @patch("api.services.feishu_export_service._fetch_tenant_access_token")
    def test_returns_error_on_runtime_error(self, mock_token, mock_create):
        from api.services.feishu_export_service import export_report_to_feishu

        mock_token.return_value = "t-fake-token"
        mock_create.side_effect = RuntimeError("Feishu auth failed: invalid app")

        result = export_report_to_feishu(_make_report())
        assert result["success"] is False
        assert "auth failed" in result["error"]


# ---------------------------------------------------------------------------
# Token caching
# ---------------------------------------------------------------------------

class TestTokenCache:
    @patch.dict("os.environ", {
        "FEISHU_APP_ID": "cli_test",
        "FEISHU_APP_SECRET": "secret_test",
    })
    @patch("api.services.feishu_export_service.requests.post")
    def test_caches_token(self, mock_post):
        from api.services.feishu_export_service import _fetch_tenant_access_token, _token_cache
        import time

        # Reset cache
        _token_cache["token"] = None
        _token_cache["expires_at"] = 0.0

        resp = MagicMock()
        resp.json.return_value = {
            "code": 0,
            "tenant_access_token": "t-cached",
            "expire": 9000,
        }
        resp.raise_for_status = MagicMock()
        mock_post.return_value = resp

        token1 = _fetch_tenant_access_token()
        token2 = _fetch_tenant_access_token()

        assert token1 == "t-cached"
        assert token2 == "t-cached"
        assert mock_post.call_count == 1  # Only one HTTP call

    @patch.dict("os.environ", {
        "FEISHU_APP_ID": "cli_test",
        "FEISHU_APP_SECRET": "secret_test",
    })
    @patch("api.services.feishu_export_service.requests.post")
    def test_refreshes_expired_token(self, mock_post):
        from api.services.feishu_export_service import _fetch_tenant_access_token, _token_cache

        _token_cache["token"] = "t-old"
        _token_cache["expires_at"] = 0.0  # Expired

        resp = MagicMock()
        resp.json.return_value = {
            "code": 0,
            "tenant_access_token": "t-new",
            "expire": 9000,
        }
        resp.raise_for_status = MagicMock()
        mock_post.return_value = resp

        token = _fetch_tenant_access_token()
        assert token == "t-new"
        assert mock_post.call_count == 1


# ---------------------------------------------------------------------------
# _create_document
# ---------------------------------------------------------------------------

class TestCreateDocument:
    @patch("api.services.feishu_export_service._feishu_headers")
    @patch("api.services.feishu_export_service.requests.post")
    def test_returns_document_info(self, mock_post, mock_headers):
        from api.services.feishu_export_service import _create_document

        mock_headers.return_value = {"Authorization": "Bearer t"}
        resp = MagicMock()
        resp.json.return_value = {
            "code": 0,
            "data": {
                "document": {
                    "document_id": "doc_123",
                    "title": "Test",
                }
            },
        }
        resp.raise_for_status = MagicMock()
        mock_post.return_value = resp

        result = _create_document("Test Title", "fld_abc")
        assert result["document_id"] == "doc_123"
        assert "doc_123" in result["url"]

    @patch("api.services.feishu_export_service._feishu_headers")
    @patch("api.services.feishu_export_service.requests.post")
    def test_raises_on_api_error(self, mock_post, mock_headers):
        from api.services.feishu_export_service import _create_document

        mock_headers.return_value = {"Authorization": "Bearer t"}
        resp = MagicMock()
        resp.json.return_value = {"code": 99991, "msg": "permission denied"}
        resp.raise_for_status = MagicMock()
        mock_post.return_value = resp

        with pytest.raises(RuntimeError, match="permission denied"):
            _create_document("Title")


# ---------------------------------------------------------------------------
# _extract_verdict
# ---------------------------------------------------------------------------

class TestExtractVerdict:
    def test_parses_valid_verdict(self):
        from api.services.feishu_export_service import _extract_verdict
        text = 'Analysis\n<!-- VERDICT: {"direction": "BULLISH", "reason": "Strong uptrend"} -->'
        v = _extract_verdict(text)
        assert v is not None
        assert v["direction"] == "看多"
        assert v["reason"] == "Strong uptrend"

    def test_returns_none_for_missing(self):
        from api.services.feishu_export_service import _extract_verdict
        assert _extract_verdict("No verdict here") is None

    def test_returns_none_for_malformed(self):
        from api.services.feishu_export_service import _extract_verdict
        assert _extract_verdict('<!-- VERDICT: {bad json -->') is None

    def test_truncates_long_reason(self):
        from api.services.feishu_export_service import _extract_verdict
        long_reason = "A" * 200
        text = f'<!-- VERDICT: {{"direction": "NEUTRAL", "reason": "{long_reason}"}} -->'
        v = _extract_verdict(text)
        assert v is not None
        assert len(v["reason"]) <= 80


# ---------------------------------------------------------------------------
# _semantic_field
# ---------------------------------------------------------------------------

class TestSemanticField:
    def test_prefers_direct_attr(self):
        from api.services.feishu_export_service import _semantic_field
        report = SimpleNamespace(research_direction="偏多", result_data=None)
        assert _semantic_field(report, "research_direction") == "偏多"

    def test_falls_back_to_result_data(self):
        from api.services.feishu_export_service import _semantic_field
        report = SimpleNamespace(
            research_direction=None,
            result_data={"research_direction": "看空"},
        )
        assert _semantic_field(report, "research_direction") == "看空"

    def test_returns_none_when_both_empty(self):
        from api.services.feishu_export_service import _semantic_field
        report = SimpleNamespace(research_direction=None, result_data=None)
        assert _semantic_field(report, "research_direction") is None


# ---------------------------------------------------------------------------
# Chunked block insertion
# ---------------------------------------------------------------------------

class TestChunkedInsertion:
    @patch.dict("os.environ", {
        "FEISHU_APP_ID": "cli_test",
        "FEISHU_APP_SECRET": "secret_test",
    })
    @patch("api.services.feishu_export_service._batch_create_blocks")
    @patch("api.services.feishu_export_service._get_document_blocks")
    @patch("api.services.feishu_export_service._create_document")
    @patch("api.services.feishu_export_service._fetch_tenant_access_token")
    def test_large_report_chunks_blocks(
        self, mock_token, mock_create, mock_get_blocks, mock_batch
    ):
        from api.services.feishu_export_service import export_report_to_feishu

        mock_token.return_value = "t-fake"

        # Create a report with many risk items to generate >50 blocks
        many_risks = [{"name": f"Risk {i}", "level": "medium", "description": f"Desc {i}"}
                      for i in range(60)]
        report = _make_report(risk_items=many_risks)

        mock_create.return_value = {
            "document_id": "doc_big",
            "url": "https://feishu.cn/docx/doc_big",
        }
        mock_get_blocks.return_value = [{"block_id": "page"}]
        mock_batch.return_value = None

        result = export_report_to_feishu(report)

        assert result["success"] is True
        # Should be called multiple times for chunking (each chunk <= 50)
        assert mock_batch.call_count >= 2


# ---------------------------------------------------------------------------
# async wrapper
# ---------------------------------------------------------------------------

class TestAsyncExport:
    @patch.dict("os.environ", {"FEISHU_APP_ID": "cli", "FEISHU_APP_SECRET": "s"})
    @patch("api.services.feishu_export_service.export_report_to_feishu")
    def test_async_calls_sync(self, mock_sync):
        import asyncio
        from api.services.feishu_export_service import export_report_to_feishu_async

        mock_sync.return_value = {"success": True, "document_url": "x", "document_token": "y", "title": "z"}

        result = asyncio.run(export_report_to_feishu_async(_make_report()))
        assert result["success"] is True
        mock_sync.assert_called_once()
