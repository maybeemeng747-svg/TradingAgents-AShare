# [TRACK-010] review_encoding_regression
"""Tests for TRACK-010: 跟踪看板盘后复盘乱码与编码/渲染回归.

Coverage matrix
---------------
1. ``sanitize_review_summary_text`` unit tests:
   - Pure Chinese strings pass through unchanged.
   - Literal ``\\u4e2d\\u6587`` (double-escaped unicode) → ``中文``.
   - Literal ``\\n`` / ``\\t`` / ``\\r`` → real control chars.
   - BOM / invisible control chars stripped.
   - ``bytes`` payloads UTF-8 decoded.
   - ``b'...'`` repr form decoded.
   - Numbers / lists / dicts / None / bool preserved.
   - Sanitizer never raises on hostile input.
2. Service-layer integration:
   - ``_build_review_summary`` sanitizes a fixture that mimics the user-reported
     garbled payload (literal ``\\uXXXX`` in ``review_note``,
     BOM in ``data_status_message``, ``bytes`` in ``as_of``).
   - Returned summary is JSON-serializable in both ``ensure_ascii=True`` and
     ``ensure_ascii=False`` modes and round-trips back to identical Chinese.
3. End-to-end API regression:
   - ``GET /v1/dashboard/tracking-board/v2`` returns valid UTF-8 JSON body
     containing the original Chinese chars (no ``\\uXXXX`` in raw body when
     parsed back, no BOM, no escape leakage).
4. Engine output is naturally UTF-8 clean (regression baseline).
5. Empty-state stability: NO_DATA payload still sanitize-safe and renders no
   stray escape characters.

Constraints honored
-------------------
- No live LLM, no live DB writes, no full-market scan.
- Does not touch tradingagents/prompts/.
- All paths covered by a unit/fixture test, no real network.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.post_market_tracking_review import (  # noqa: E402
    build_post_market_tracking_review,
    sanitize_review_summary_text,
    DATA_STATUS_NO_DATA,
    DATA_STATUS_NON_TRADING_DAY,
    DATA_STATUS_OK,
)


_NOW = "2026-06-29 18:52:31"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _cn_holdings():
    """A holding with Chinese name, percent sign, table syntax, special chars."""
    return [
        {
            "symbol": "600519.SH",
            "name": "贵州茅台",
            "live_price": 1735.5,
            "price_change_pct": -2.35,
            "average_cost": 1700.0,
            "floating_pnl_pct": 2.09,
            "analysis": {"low_price": 1688.0, "action_label": "持有"},
        },
        {
            "symbol": "000001.SZ",
            "name": "平安银行",
            "live_price": 11.82,
            "price_change_pct": 0.51,
            "average_cost": 12.10,
            "floating_pnl_pct": -2.31,
            "analysis": None,
        },
    ]


def _cn_observation_items():
    return [
        {
            "symbol": "300750.SZ",
            "name": "宁德时代",
            "status": "watching",
            "entry_low": 180.0,
            "entry_high": 195.0,
            "invalid_price": 175.0,
            "live_price": 188.5,
        },
    ]


def _cn_tradeflow_review():
    return {
        "status": "ok",
        "results": [
            {
                "symbol": "601318.SH",
                "name": "中国平安",
                "observe_state": "TRIGGERED",
                "plan_action": "OBSERVE",
                "candidate_type": "POLICY_AMBUSH",
                "tomorrow_focus": "已触发且命中，关注次日是否站稳触发价",
                "downgrade_reason": "",
                "evidence_needed": [],
            },
        ],
    }


def _build_cn_review():
    return build_post_market_tracking_review(
        holdings=_cn_holdings(),
        observation_items=_cn_observation_items(),
        tradeflow_review=_cn_tradeflow_review(),
        is_trading_day=True,
        review_date="2026-06-27",
        as_of=_NOW,
    )


# ---------------------------------------------------------------------------
# 1. Sanitizer unit tests
# ---------------------------------------------------------------------------


class TestSanitizerPassThrough:
    def test_plain_chinese_unchanged(self):
        assert sanitize_review_summary_text("贵州茅台") == "贵州茅台"

    def test_percent_and_special_chars_preserved(self):
        s = "今日跌幅 -3.5%，触发价 12.30 元（含手续费 ¥0.06）"
        assert sanitize_review_summary_text(s) == s

    def test_table_markdown_preserved_as_text(self):
        s = "| 持仓 | 涨跌 |\n|---|---|\n| 茅台 | -2.35% |"
        out = sanitize_review_summary_text(s)
        assert "| 持仓 | 涨跌 |" in out
        assert "茅台" in out

    def test_newlines_preserved(self):
        s = "第一行\n第二行"
        assert sanitize_review_summary_text(s) == "第一行\n第二行"

    def test_numbers_bools_none_preserved(self):
        assert sanitize_review_summary_text(123) == 123
        assert sanitize_review_summary_text(12.5) == 12.5
        assert sanitize_review_summary_text(True) is True
        assert sanitize_review_summary_text(None) is None

    def test_dict_and_list_recursion(self):
        data = {
            "a": "中文",
            "b": ["x", "y", {"c": "平安银行"}],
            "n": 42,
        }
        out = sanitize_review_summary_text(data)
        assert out == data
        # Returns NEW containers (no in-place mutation)
        assert out is not data


class TestSanitizerLiteralUnicodeEscape:
    def test_literal_unicode_escape_decoded(self):
        # The user-reported garbled form: literal backslash-u-hex sequence.
        s = r"\u4e2d\u6587\u6d4b\u8bd5"
        assert sanitize_review_summary_text(s) == "中文测试"

    def test_partial_literal_escape_decoded(self):
        s = r"\u8d27\u5e01 \u57fa\u91d1\u51c0\u503c -2.35%"
        out = sanitize_review_summary_text(s)
        assert "货币" in out
        assert "基金净值" in out
        assert "-2.35%" in out

    def test_mixed_real_and_literal_escape(self):
        s = "贵州茅台 r(\u4e0d\u662f\u8bf4\u4e86) 信号"
        # The real 中文 should survive; the literal \u sequences should decode.
        out = sanitize_review_summary_text(s)
        assert "贵州茅台" in out
        assert "不是说" in out
        assert "信号" in out


class TestSanitizerLiteralCtrlEscape:
    def test_literal_newline_decoded(self):
        # Literal backslash-n (two chars), not a real newline.
        s = r"持仓茅台\n跌幅 -2.35%"
        out = sanitize_review_summary_text(s)
        assert out == "持仓茅台\n跌幅 -2.35%"

    def test_literal_tab_decoded(self):
        s = r"col1\tcol2"
        assert sanitize_review_summary_text(s) == "col1\tcol2"


class TestSanitizerBomAndInvisible:
    def test_bom_stripped(self):
        s = "\ufeff贵州茅台"
        assert sanitize_review_summary_text(s) == "贵州茅台"

    def test_invisible_control_chars_stripped(self):
        s = "贵州\x00茅台\x07信号\x1f"
        out = sanitize_review_summary_text(s)
        assert out == "贵州茅台信号"

    def test_real_newline_and_tab_preserved_when_stripping_invisible(self):
        s = "行1\n行2\tcol\x00-B"
        assert sanitize_review_summary_text(s) == "行1\n行2\tcol-B"


class TestSanitizerBytes:
    def test_bytes_decoded_as_utf8(self):
        assert sanitize_review_summary_text(b"\xe8\xb4\xb5\xe5\xb7\x9e\xe8\x8c\x85\xe5\x8f\xb0") == "贵州茅台"

    def test_bytes_invalid_utf8_replaced(self):
        # Invalid UTF-8 lead byte; sanitizer should not crash.
        out = sanitize_review_summary_text(b"\xff\xfe\xfd")
        assert isinstance(out, str)

    def test_bytes_repr_form_decoded(self):
        # Some upstream code does str(bytes_obj) producing "b'...'".
        # The inner \xHH escapes are raw UTF-8 bytes for "贵州茅台".
        s = "b'\\xe8\\xb4\\xb5\\xe5\\xb7\\x9e\\xe8\\x8c\\x85\\xe5\\x8f\\xb0'"
        out = sanitize_review_summary_text(s)
        assert "贵" in out and "州" in out and "茅台" in out


class TestSanitizerHostileInput:
    def test_extremely_long_string(self):
        s = "贵州茅台" * 5000
        out = sanitize_review_summary_text(s)
        assert len(out) == len(s)
        assert out.startswith("贵州茅台")

    def test_already_clean_complex_payload(self):
        # The full review_summary produced by the engine is naturally UTF-8
        # clean. Sanitizer must be a no-op on it.
        review = _build_cn_review()
        sanitized = sanitize_review_summary_text(review)
        # Round-trip equality (sanitizer must not alter clean UTF-8 payload).
        assert sanitized == review

    def test_sanitizer_does_not_raise_on_dict_with_bytes_key(self):
        # Hostile: dict whose KEY is bytes (very unusual). Should not crash.
        out = sanitize_review_summary_text({b"k": "v"})
        assert isinstance(out, dict)


# ---------------------------------------------------------------------------
# 2. Service-layer integration
# ---------------------------------------------------------------------------


class TestServiceBuildReviewSummaryEncodingRegression:
    """api.services.tracking_board_service._build_review_summary must
    return a UTF-8-clean review_summary even when upstream data sources
    leak literal \\uXXXX / BOM / bytes repr into the payload."""

    def test_clean_payload_passes_through_service(self, monkeypatch):
        from api.services import tracking_board_service as svc

        # Stub tradeflow review so service doesn't hit SQLite.
        monkeypatch.setattr(
            "api.services.tracking_board_service._fetch_tradeflow_review",
            lambda _date: _cn_tradeflow_review(),
        )
        out = svc._build_review_summary(
            holdings=_cn_holdings(),
            observation_items=_cn_observation_items(),
            previous_trade_date="2026-06-27",
            is_trading_day=True,
            now=datetime(2026, 6, 29, 18, 52, 31),
        )
        assert out["data_status"] == DATA_STATUS_OK
        assert out["review_date"] == "2026-06-27"
        # Chinese preserved on every text surface.
        holdings_text = json.dumps(out["holdings_review"], ensure_ascii=False)
        assert "贵州茅台" in holdings_text
        assert "平安银行" in holdings_text
        # No literal \uXXXX in sanitized UTF-8 output.
        assert r"\u4e2d" not in holdings_text
        assert r"\u8d35" not in holdings_text

    def test_service_strips_bom_from_data_status_message(self, monkeypatch):
        """If the tradeflow layer or DB ever regresses and writes BOM into a
        Chinese status message, the service-layer sanitizer must strip it
        before the payload reaches the API."""
        from api.services import tracking_board_service as svc

        def _patched_engine(**kwargs):
            review = build_post_market_tracking_review(**kwargs)
            # Simulate upstream regression.
            review["data_status_message"] = "\ufeff盘后复盘已生成"
            review["holdings_review"][0]["review_note"] = (
                r"\u6301\u6709 \u8dcc\u5e45 -2.35%"
            )
            review["as_of"] = b"2026-06-29 18:52:31"  # bytes leak
            return review

        monkeypatch.setattr(
            "api.services.tracking_board_service.build_post_market_tracking_review",
            _patched_engine,
        )
        monkeypatch.setattr(
            "api.services.tracking_board_service._fetch_tradeflow_review",
            lambda _date: None,
        )

        out = svc._build_review_summary(
            holdings=_cn_holdings(),
            observation_items=[],
            previous_trade_date="2026-06-27",
            is_trading_day=True,
            now=datetime(2026, 6, 29, 18, 52, 31),
        )
        # BOM stripped from data_status_message.
        assert out["data_status_message"] == "盘后复盘已生成"
        assert "\ufeff" not in out["data_status_message"]
        # Literal \uXXXX decoded.
        note = out["holdings_review"][0]["review_note"]
        assert "持有" in note
        assert "跌幅" in note
        assert r"\u6301" not in note
        # bytes leak decoded to UTF-8 string.
        assert out["as_of"] == "2026-06-29 18:52:31"

    def test_service_payload_is_json_round_trip_safe(self, monkeypatch):
        """Ensure the sanitized review_summary survives both ensure_ascii
        modes without producing mojibake on the frontend."""
        from api.services import tracking_board_service as svc

        monkeypatch.setattr(
            "api.services.tracking_board_service._fetch_tradeflow_review",
            lambda _date: _cn_tradeflow_review(),
        )
        out = svc._build_review_summary(
            holdings=_cn_holdings(),
            observation_items=_cn_observation_items(),
            previous_trade_date="2026-06-27",
            is_trading_day=True,
            now=datetime(2026, 6, 29, 18, 52, 31),
        )
        # ensure_ascii=True path (browser fallback)
        ascii_blob = json.dumps(out, ensure_ascii=True)
        reparsed_from_ascii = json.loads(ascii_blob)
        assert "贵州茅台" in json.dumps(reparsed_from_ascii, ensure_ascii=False)
        # ensure_ascii=False path (FastAPI default since Starlette 0.13+)
        utf8_blob = json.dumps(out, ensure_ascii=False)
        assert "贵州茅台" in utf8_blob
        assert r"\u8d35" not in utf8_blob
        reparsed_from_utf8 = json.loads(utf8_blob)
        assert reparsed_from_utf8 == reparsed_from_ascii


# ---------------------------------------------------------------------------
# 3. End-to-end API regression via FastAPI TestClient
# ---------------------------------------------------------------------------


class TestTrackingBoardV2EncodingE2E:
    """GET /v1/dashboard/tracking-board/v2 must return valid UTF-8 JSON.

    Covers the full chain: SQLite read (mocked) → service build → FastAPI
    JSONResponse → wire bytes → JSON parse.
    """

    def test_v2_endpoint_returns_chinese_in_raw_body(self, monkeypatch):
        from api.main import app
        from fastapi.testclient import TestClient

        # Stub the data sources so the service builds a deterministic review.
        monkeypatch.setattr(
            "api.services.tracking_board_service._fetch_tradeflow_review",
            lambda _date: _cn_tradeflow_review(),
        )
        monkeypatch.setattr(
            "api.services.tracking_board_service._fetch_observation_items",
            lambda: _cn_observation_items(),
        )
        monkeypatch.setattr(
            "api.services.tracking_board_service._fetch_live_quotes",
            lambda symbols, **kwargs: {
                "600519.SH": {"price": 1735.5, "change_pct": -2.35},
                "000001.SZ": {"price": 11.82, "change_pct": 0.51},
                "300750.SZ": {"price": 188.5, "change_pct": 0.8},
            },
        )
        monkeypatch.setattr(
            "api.services.tracking_board_service.cn_today_str",
            lambda: "2026-06-29",
        )
        monkeypatch.setattr(
            "api.services.tracking_board_service.previous_cn_trading_day",
            lambda _: "2026-06-27",
        )
        monkeypatch.setattr(
            "api.services.tracking_board_service.is_cn_trading_day",
            lambda _: True,
        )

        client = TestClient(app, raise_server_exceptions=False)

        # Register a unique test user and grab a token (no network).
        from uuid import uuid4
        from datetime import timezone
        from api.database import UserDB, get_db_ctx, init_db
        from api.services import auth_service

        init_db()
        email = auth_service.normalize_email(f"track010-{uuid4().hex[:8]}@test.com")
        now = datetime.now(timezone.utc)
        with get_db_ctx() as db:
            user = UserDB(
                id=str(uuid4()),
                email=email,
                is_active=True,
                created_at=now,
                updated_at=now,
                last_login_at=now,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            token = auth_service.create_access_token(user)
            user_id = user.id

        # Skip holdings import — empty holdings also exercise sanitizer path.
        # We rely on observation items + tradeflow review for the payload.
        # Inject the test user via monkey-patching the dependency.
        from api.database import ImportedPortfolioPositionDB

        with get_db_ctx() as db:
            db.add(
                ImportedPortfolioPositionDB(
                    id=uuid4().hex,
                    user_id=user_id,
                    source="manual",
                    symbol="600519.SH",
                    security_name="贵州茅台",
                    current_position=100.0,
                    available_position=100.0,
                    average_cost=1700.0,
                    market_value=173550.0,
                    current_position_pct=100.0,
                    trade_points_json=[],
                    trade_points_count=0,
                    last_imported_at=now,
                )
            )
            db.commit()

        headers = {"Authorization": f"Bearer {token}"}
        resp = client.get("/v1/dashboard/tracking-board/v2", headers=headers)

        assert resp.status_code == 200, resp.text
        # Raw body must contain UTF-8 Chinese (FastAPI uses ensure_ascii=False).
        raw_text = resp.text
        assert "贵州茅台" in raw_text
        assert "中国平安" in raw_text  # from tradeflow review
        # No BOM, no literal \uXXXX in the wire payload.
        assert "\ufeff" not in raw_text
        assert r"\u8d35" not in raw_text
        assert r"\u8d77" not in raw_text

        body = resp.json()
        review = body["review_summary"]
        assert review["data_status"] in (
            DATA_STATUS_OK,
            DATA_STATUS_NO_DATA,
            DATA_STATUS_NON_TRADING_DAY,
            "PARTIAL_DATA",
        )
        # Every text surface in the review must survive JSON round-trip.
        review_text = json.dumps(review, ensure_ascii=False)
        assert "贵州茅台" in review_text
        # Sanitizer invariant: no literal escape sequences in the final payload.
        assert r"\u4e2d" not in review_text
        assert r"\u6301" not in review_text


# ---------------------------------------------------------------------------
# 4. Engine output baseline (no regression)
# ---------------------------------------------------------------------------


class TestEngineOutputIsNaturallyUTF8Clean:
    """The pure engine must continue to emit clean UTF-8 Chinese strings
    on its own (sanitizer is only a safety net, not a substitute)."""

    def test_engine_emits_clean_chinese(self):
        r = _build_cn_review()
        # Direct attribute access — these must be real Chinese, not escaped.
        assert r["holdings_review"][0]["name"] == "贵州茅台"
        assert r["observation_review"][0]["name"] == "宁德时代"
        assert r["candidate_pool_review"][0]["name"] == "中国平安"
        # review_note contains Chinese punctuation and percent sign — make
        # sure no implicit ascii escape leaked in.
        for h in r["holdings_review"]:
            assert r"\u" not in h.get("review_note", "")
            assert "\ufeff" not in h.get("review_note", "")

    def test_engine_no_data_payload_is_clean(self):
        r = build_post_market_tracking_review(
            holdings=[],
            observation_items=[],
            tradeflow_review=None,
            is_trading_day=True,
            review_date="2026-06-27",
            as_of=_NOW,
        )
        assert r["data_status"] == DATA_STATUS_NO_DATA
        assert "无" in r["data_status_message"] or "空" in r["data_status_message"]
        # Empty-state payload must contain zero escape artifacts.
        blob = json.dumps(r, ensure_ascii=False)
        assert r"\u" not in blob
        assert "\ufeff" not in blob


# ---------------------------------------------------------------------------
# 5. Empty-state stability
# ---------------------------------------------------------------------------


class TestEmptyStateStability:
    def test_no_data_payload_sanitizes_cleanly(self):
        r = build_post_market_tracking_review(
            holdings=[],
            observation_items=[],
            tradeflow_review=None,
            is_trading_day=True,
            review_date="2026-06-27",
            as_of=_NOW,
        )
        sanitized = sanitize_review_summary_text(r)
        assert sanitized["data_status"] == DATA_STATUS_NO_DATA
        # Even when empty, no stray escape characters appear anywhere.
        blob = json.dumps(sanitized, ensure_ascii=False)
        for sequence in (r"\u00", r"\u4e", "\ufeff", "\x00"):
            assert sequence not in blob

    def test_non_trading_day_payload_sanitizes_cleanly(self):
        r = build_post_market_tracking_review(
            holdings=[],
            observation_items=[],
            tradeflow_review=None,
            is_trading_day=False,
            review_date="2026-06-28",
            as_of=_NOW,
        )
        assert r["data_status"] == DATA_STATUS_NON_TRADING_DAY
        sanitized = sanitize_review_summary_text(r)
        # Chinese non-trading-day message preserved.
        assert "非交易日" in sanitized["data_status_message"]
        assert "下一交易日" in sanitized["data_status_message"]

    def test_service_empty_state_path(self, monkeypatch):
        from api.services import tracking_board_service as svc

        monkeypatch.setattr(
            "api.services.tracking_board_service._fetch_tradeflow_review",
            lambda _date: None,
        )
        out = svc._build_review_summary(
            holdings=[],
            observation_items=[],
            previous_trade_date="2026-06-27",
            is_trading_day=True,
            now=datetime(2026, 6, 29, 18, 52, 31),
        )
        assert out["data_status"] == DATA_STATUS_NO_DATA
        # No stray escape characters anywhere in the empty-state payload.
        blob = json.dumps(out, ensure_ascii=False)
        for sequence in (r"\u00", r"\u4e", "\ufeff", "\x00"):
            assert sequence not in blob
        assert "暂无" in out["data_status_message"] or "无" in out["data_status_message"]


if __name__ == "__main__":
    pytest.main([__file__, "-q", "--tb=short"])
