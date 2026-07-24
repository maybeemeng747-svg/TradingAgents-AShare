"""Tests for api.services.notification_confirmation_service (M-010).

Covers:
- generate_pending: drafts saved to DB, dedup by (user, symbol, event_type)
- list_pending: ordered by priority, filtered by status
- list_log: history with optional status filter
- confirm_and_send: state transitions, webhook send, error handling
- dismiss: state transition
- confirm_all_pending: batch confirm and send
- Edge cases: invalid id, wrong status, duplicate generate
"""
from __future__ import annotations

import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base, NotificationLogDB


# ---------------------------------------------------------------------------
# In-memory DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


# ---------------------------------------------------------------------------
# Mock draft data
# ---------------------------------------------------------------------------

_MOCK_DRY_RUN = {
    "schema_version": "1.0",
    "as_of": "2026-07-23 10:00:00",
    "dry_run": True,
    "intraday_push": [
        {
            "event_type": "holdings_risk_large_drop",
            "priority": "P0",
            "symbol": "600519.SH",
            "name": "贵州茅台",
            "title": "贵州茅台 大幅下跌",
            "reason": "当日跌幅超过5%",
            "source": "holdings_snapshot",
            "as_of": "2026-07-23 10:00:00",
            "record_only": False,
        },
        {
            "event_type": "observation_in_entry_zone",
            "priority": "P1",
            "symbol": "000001.SZ",
            "name": "平安银行",
            "title": "平安银行 进入买入区间",
            "reason": "价格回落至12.5-13.0入场区间",
            "source": "observation_warehouse",
            "as_of": "2026-07-23 10:00:00",
            "record_only": False,
        },
    ],
    "daily_digest": [
        {
            "event_type": "mandate_daily_digest",
            "priority": "P2",
            "symbol": "",
            "name": "",
            "title": "昊天日报摘要",
            "reason": "今日3个主题升温",
            "source": "mandate_daily_report",
            "as_of": "2026-07-23 10:00:00",
            "record_only": False,
        },
    ],
    "recorded_only": [],
    "deduplicated": [],
    "summary_counts": {
        "total_drafts": 3,
        "intraday_push": 2,
        "daily_digest": 1,
        "recorded_only": 0,
        "deduplicated": 0,
    },
    "markdown_preview": "# preview",
    "json_preview": [],
    "runtime_tier_meta": {},
    "context_data_status": {},
    "mandate_daily_digest": {},
    "data_blocker_digest": {},
}


# ---------------------------------------------------------------------------
# generate_pending tests
# ---------------------------------------------------------------------------


class TestGeneratePending:
    def test_request_model_defers_channel_validation_to_service(self):
        from api.main import NotificationGenerateRequest

        assert NotificationGenerateRequest(channel="Feishu").channel == "Feishu"
        assert NotificationGenerateRequest(channel="bark").channel == "bark"

    def test_generates_drafts_to_db(self, db_session):
        with patch(
            "api.services.notification_confirmation_service.build_notification_dry_run",
            return_value=_MOCK_DRY_RUN,
        ):
            from api.services.notification_confirmation_service import generate_pending

            result = generate_pending(db_session, "user-1", channel="feishu")

            assert result["generated_count"] == 3
            assert result["skipped_count"] == 0
            assert len(result["pending_items"]) == 3

            # Verify DB rows
            rows = db_session.query(NotificationLogDB).all()
            assert len(rows) == 3
            assert all(r.status == "pending_confirmation" for r in rows)
            assert all(r.user_id == "user-1" for r in rows)
            assert all(r.channel == "feishu" for r in rows)

    def test_dedup_by_symbol_event_type(self, db_session):
        with patch(
            "api.services.notification_confirmation_service.build_notification_dry_run",
            return_value=_MOCK_DRY_RUN,
        ):
            from api.services.notification_confirmation_service import generate_pending

            # First call: 3 generated
            result1 = generate_pending(db_session, "user-1")
            assert result1["generated_count"] == 3

            # Second call: all 3 skipped (same user, symbol, event_type)
            result2 = generate_pending(db_session, "user-1")
            assert result2["generated_count"] == 0
            assert result2["skipped_count"] == 3

    def test_different_users_not_deduped(self, db_session):
        with patch(
            "api.services.notification_confirmation_service.build_notification_dry_run",
            return_value=_MOCK_DRY_RUN,
        ):
            from api.services.notification_confirmation_service import generate_pending

            result1 = generate_pending(db_session, "user-1")
            assert result1["generated_count"] == 3

            result2 = generate_pending(db_session, "user-2")
            assert result2["generated_count"] == 3

            rows = db_session.query(NotificationLogDB).all()
            assert len(rows) == 6

    def test_force_refresh_cleared_dedup(self, db_session):
        with patch(
            "api.services.notification_confirmation_service.build_notification_dry_run",
            return_value=_MOCK_DRY_RUN,
        ):
            from api.services.notification_confirmation_service import generate_pending

            result1 = generate_pending(db_session, "user-1")
            assert result1["generated_count"] == 3

            # After confirming one, generate again with force_refresh
            from api.services.notification_confirmation_service import confirm_and_send
            items = result1["pending_items"]
            # Confirm one to move it out of pending
            with patch(
                "api.services.notification_confirmation_service._do_send",
                return_value={"success": True, "notification": {}},
            ):
                confirm_and_send(db_session, items[0]["id"], "user-1")

            # Now generate again — should create 1 new (the confirmed one)
            result2 = generate_pending(db_session, "user-1")
            assert result2["generated_count"] == 1  # re-generated the confirmed one

    def test_empty_drafts(self, db_session):
        empty_run = {**_MOCK_DRY_RUN, "intraday_push": [], "daily_digest": []}
        with patch(
            "api.services.notification_confirmation_service.build_notification_dry_run",
            return_value=empty_run,
        ):
            from api.services.notification_confirmation_service import generate_pending

            result = generate_pending(db_session, "user-1")
            assert result["generated_count"] == 0
            assert result["skipped_count"] == 0
            assert len(result["pending_items"]) == 0


# ---------------------------------------------------------------------------
# list_pending tests
# ---------------------------------------------------------------------------


class TestListPending:
    def test_returns_pending_only(self, db_session):
        with patch(
            "api.services.notification_confirmation_service.build_notification_dry_run",
            return_value=_MOCK_DRY_RUN,
        ):
            from api.services.notification_confirmation_service import generate_pending, list_pending

            generate_pending(db_session, "user-1")
            pending = list_pending(db_session, "user-1")
            assert len(pending) == 3
            assert all(p["status"] == "pending_confirmation" for p in pending)

    def test_ordered_by_priority(self, db_session):
        with patch(
            "api.services.notification_confirmation_service.build_notification_dry_run",
            return_value=_MOCK_DRY_RUN,
        ):
            from api.services.notification_confirmation_service import generate_pending, list_pending

            generate_pending(db_session, "user-1")
            pending = list_pending(db_session, "user-1")
            # P0 < P1 < P2 in string sort
            priorities = [p["priority"] for p in pending]
            assert priorities == sorted(priorities)

    def test_empty_when_no_pending(self, db_session):
        from api.services.notification_confirmation_service import list_pending

        pending = list_pending(db_session, "user-1")
        assert pending == []

    def test_respects_limit(self, db_session):
        with patch(
            "api.services.notification_confirmation_service.build_notification_dry_run",
            return_value=_MOCK_DRY_RUN,
        ):
            from api.services.notification_confirmation_service import generate_pending, list_pending

            generate_pending(db_session, "user-1")
            pending = list_pending(db_session, "user-1", limit=2)
            assert len(pending) == 2


# ---------------------------------------------------------------------------
# list_log tests
# ---------------------------------------------------------------------------


class TestListLog:
    def test_returns_all_statuses(self, db_session):
        with patch(
            "api.services.notification_confirmation_service.build_notification_dry_run",
            return_value=_MOCK_DRY_RUN,
        ):
            from api.services.notification_confirmation_service import (
                generate_pending,
                list_log,
                dismiss,
            )

            generate_pending(db_session, "user-1")
            items = list_log(db_session, "user-1")
            assert len(items) == 3

            # Dismiss one
            dismiss(db_session, items[0]["id"], "user-1")

            all_items = list_log(db_session, "user-1")
            assert len(all_items) == 3

    def test_filter_by_status(self, db_session):
        with patch(
            "api.services.notification_confirmation_service.build_notification_dry_run",
            return_value=_MOCK_DRY_RUN,
        ):
            from api.services.notification_confirmation_service import (
                generate_pending,
                list_log,
                dismiss,
            )

            generate_pending(db_session, "user-1")
            items = list_log(db_session, "user-1")
            dismiss(db_session, items[0]["id"], "user-1")

            dismissed = list_log(db_session, "user-1", status="dismissed")
            assert len(dismissed) == 1

            pending = list_log(db_session, "user-1", status="pending_confirmation")
            assert len(pending) == 2


# ---------------------------------------------------------------------------
# confirm_and_send tests
# ---------------------------------------------------------------------------


class TestConfirmAndSend:
    def test_confirm_sends_via_webhook(self, db_session):
        with patch(
            "api.services.notification_confirmation_service.build_notification_dry_run",
            return_value=_MOCK_DRY_RUN,
        ):
            from api.services.notification_confirmation_service import (
                generate_pending,
                confirm_and_send,
            )

            generate_pending(db_session, "user-1")
            items = db_session.query(NotificationLogDB).filter(
                NotificationLogDB.status == "pending_confirmation"
            ).all()

            with patch(
                "api.services.notification_confirmation_service._do_send",
                return_value={"success": True, "notification": {"id": items[0].id, "status": "sent"}},
            ):
                result = confirm_and_send(db_session, items[0].id, "user-1")
                assert result["success"] is True

    def test_confirm_invalid_id(self, db_session):
        from api.services.notification_confirmation_service import confirm_and_send

        result = confirm_and_send(db_session, "nonexistent-id", "user-1")
        assert result["success"] is False
        assert "不存在" in result["error"]

    def test_confirm_wrong_status(self, db_session):
        with patch(
            "api.services.notification_confirmation_service.build_notification_dry_run",
            return_value=_MOCK_DRY_RUN,
        ):
            from api.services.notification_confirmation_service import (
                generate_pending,
                confirm_and_send,
                dismiss,
            )

            generate_pending(db_session, "user-1")
            items = db_session.query(NotificationLogDB).all()
            # Dismiss first
            dismiss(db_session, items[0].id, "user-1")

            # Try to confirm dismissed
            result = confirm_and_send(db_session, items[0].id, "user-1")
            assert result["success"] is False
            assert "dismissed" in result["error"]

    def test_send_failure_marked(self, db_session):
        with patch(
            "api.services.notification_confirmation_service.build_notification_dry_run",
            return_value=_MOCK_DRY_RUN,
        ):
            from api.services.notification_confirmation_service import (
                generate_pending,
                confirm_and_send,
            )

            generate_pending(db_session, "user-1")
            items = db_session.query(NotificationLogDB).all()

            with patch(
                "api.services.notification_confirmation_service._do_send",
                return_value={"success": False, "error": "webhook 未配置", "notification": {}},
            ):
                result = confirm_and_send(db_session, items[0].id, "user-1")
                assert result["success"] is False


# ---------------------------------------------------------------------------
# dismiss tests
# ---------------------------------------------------------------------------


class TestDismiss:
    def test_dismiss_changes_status(self, db_session):
        with patch(
            "api.services.notification_confirmation_service.build_notification_dry_run",
            return_value=_MOCK_DRY_RUN,
        ):
            from api.services.notification_confirmation_service import generate_pending, dismiss

            generate_pending(db_session, "user-1")
            items = db_session.query(NotificationLogDB).all()

            result = dismiss(db_session, items[0].id, "user-1")
            assert result["success"] is True
            assert result["notification"]["status"] == "dismissed"

            row = db_session.query(NotificationLogDB).filter(
                NotificationLogDB.id == items[0].id
            ).first()
            assert row.status == "dismissed"

    def test_dismiss_invalid_id(self, db_session):
        from api.services.notification_confirmation_service import dismiss

        result = dismiss(db_session, "nonexistent-id", "user-1")
        assert result["success"] is False

    def test_dismiss_already_sent_fails(self, db_session):
        # Create a sent notification directly
        entry = NotificationLogDB(
            id="test-sent-1",
            user_id="user-1",
            channel="feishu",
            event_type="test",
            priority="P1",
            title="test",
            status="sent",
        )
        db_session.add(entry)
        db_session.commit()

        from api.services.notification_confirmation_service import dismiss
        result = dismiss(db_session, "test-sent-1", "user-1")
        assert result["success"] is False
        assert "sent" in result["error"]


# ---------------------------------------------------------------------------
# confirm_all_pending tests
# ---------------------------------------------------------------------------


class TestConfirmAllPending:
    def test_batch_confirm(self, db_session):
        with patch(
            "api.services.notification_confirmation_service.build_notification_dry_run",
            return_value=_MOCK_DRY_RUN,
        ):
            from api.services.notification_confirmation_service import (
                generate_pending,
                confirm_all_pending,
            )

            generate_pending(db_session, "user-1")

            with patch(
                "api.services.notification_confirmation_service._do_send",
                return_value={"success": True, "notification": {}},
            ):
                result = confirm_all_pending(db_session, "user-1")
                assert result["total"] == 3
                assert result["sent"] == 3
                assert result["failed"] == 0

    def test_partial_failure(self, db_session):
        with patch(
            "api.services.notification_confirmation_service.build_notification_dry_run",
            return_value=_MOCK_DRY_RUN,
        ):
            from api.services.notification_confirmation_service import (
                generate_pending,
                confirm_all_pending,
            )

            generate_pending(db_session, "user-1")

            call_count = 0
            def mock_send(db, row):
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    return {"success": False, "error": "test failure", "notification": {}}
                return {"success": True, "notification": {}}

            with patch(
                "api.services.notification_confirmation_service._do_send",
                side_effect=mock_send,
            ):
                result = confirm_all_pending(db_session, "user-1")
                assert result["total"] == 3
                assert result["sent"] == 2
                assert result["failed"] == 1
                assert len(result["errors"]) == 1

    def test_empty_queue(self, db_session):
        from api.services.notification_confirmation_service import confirm_all_pending

        result = confirm_all_pending(db_session, "user-1")
        assert result["total"] == 0
        assert result["sent"] == 0


# ---------------------------------------------------------------------------
# _do_send tests
# ---------------------------------------------------------------------------


class TestDoSend:
    def test_success_path(self, db_session):
        entry = NotificationLogDB(
            id="test-send-1",
            user_id="user-1",
            channel="feishu",
            event_type="holdings_risk",
            priority="P0",
            symbol="600519.SH",
            name="贵州茅台",
            title="测试通知",
            reason="test reason",
            status="confirmed",
            payload={"symbol": "600519.SH", "event_type": "holdings_risk"},
        )
        db_session.add(entry)
        db_session.commit()

        with patch.dict(os.environ, {
            "FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/abc",
            "FEISHU_WEBHOOK_ENABLED": "true",
        }):
            with patch("api.services.feishu_webhook_service.send_message", return_value=True):
                from api.services.notification_confirmation_service import _do_send
                result = _do_send(db_session, entry)
                assert result["success"] is True

                db_session.refresh(entry)
                assert entry.status == "sent"
                assert entry.sent_at is not None

    def test_webhook_not_configured(self, db_session):
        entry = NotificationLogDB(
            id="test-send-2",
            user_id="user-1",
            channel="feishu",
            event_type="test",
            priority="P1",
            title="test",
            status="confirmed",
        )
        db_session.add(entry)
        db_session.commit()

        with patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": "", "FEISHU_WEBHOOK_ENABLED": "true"}, clear=False):
            from api.services.notification_confirmation_service import _do_send
            result = _do_send(db_session, entry)
            assert result["success"] is False
            assert "未配置" in result["error"]

            db_session.refresh(entry)
            assert entry.status == "failed"

    def test_send_api_failure(self, db_session):
        entry = NotificationLogDB(
            id="test-send-3",
            user_id="user-1",
            channel="feishu",
            event_type="test",
            priority="P1",
            title="test",
            status="confirmed",
            payload={"symbol": "000001.SZ"},
        )
        db_session.add(entry)
        db_session.commit()

        with patch.dict(os.environ, {
            "FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/abc",
            "FEISHU_WEBHOOK_ENABLED": "true",
        }):
            with patch("api.services.feishu_webhook_service.send_message", return_value=False):
                from api.services.notification_confirmation_service import _do_send
                result = _do_send(db_session, entry)
                assert result["success"] is False
                assert "失败" in result["error"]

    def test_send_exception(self, db_session):
        entry = NotificationLogDB(
            id="test-send-4",
            user_id="user-1",
            channel="feishu",
            event_type="test",
            priority="P1",
            title="test",
            status="confirmed",
        )
        db_session.add(entry)
        db_session.commit()

        with patch.dict(os.environ, {
            "FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/abc",
            "FEISHU_WEBHOOK_ENABLED": "true",
        }):
            with patch("api.services.feishu_webhook_service.send_message", side_effect=ConnectionError("timeout")):
                from api.services.notification_confirmation_service import _do_send
                result = _do_send(db_session, entry)
                assert result["success"] is False
                assert "timeout" in result["error"]

    # ── [M-010-R1] channel guard & exception sanitization ───────────────────

    def test_m010_r1_non_feishu_channel_blocked(self, db_session):
        """Non-feishu channel must NOT fall through to feishu sending."""
        entry = NotificationLogDB(
            id="test-send-r1-bark",
            user_id="user-1",
            channel="bark",  # not feishu
            event_type="test",
            priority="P1",
            title="test",
            status="confirmed",
            payload={"symbol": "000001.SZ"},
        )
        db_session.add(entry)
        db_session.commit()

        with patch.dict(os.environ, {
            "FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/abc",
            "FEISHU_WEBHOOK_ENABLED": "true",
        }):
            # send_message must never be reached for a non-feishu channel
            with patch(
                "api.services.feishu_webhook_service.send_message",
                side_effect=AssertionError("send_message should not be called"),
            ):
                from api.services.notification_confirmation_service import _do_send
                result = _do_send(db_session, entry)
                assert result["success"] is False
                assert "渠道" in result["error"]

                db_session.refresh(entry)
                assert entry.status == "failed"
                assert entry.webhook_url_masked is None  # never assigned

    def test_m010_r1_non_feishu_channel_case_insensitive(self, db_session):
        """``Feishu`` / ``FEISHU`` (case variants) are still accepted."""
        entry = NotificationLogDB(
            id="test-send-r1-feishu-ci",
            user_id="user-1",
            channel="Feishu",  # case variant
            event_type="test",
            priority="P1",
            title="test",
            status="confirmed",
            payload={"symbol": "000001.SZ"},
        )
        db_session.add(entry)
        db_session.commit()

        with patch.dict(os.environ, {
            "FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/abc",
            "FEISHU_WEBHOOK_ENABLED": "true",
        }):
            with patch("api.services.feishu_webhook_service.send_message", return_value=True):
                from api.services.notification_confirmation_service import _do_send
                result = _do_send(db_session, entry)
                assert result["success"] is True

    def test_m010_r1_exception_token_scrubbed_from_db(self, db_session):
        """Exception text with a webhook URL must be scrubbed before persisting."""
        entry = NotificationLogDB(
            id="test-send-r1-scrub",
            user_id="user-1",
            channel="feishu",
            event_type="test",
            priority="P1",
            title="test",
            status="confirmed",
        )
        db_session.add(entry)
        db_session.commit()

        token = "abcdefgh1234567890"
        leaky_msg = (
            f"HTTPSConnectionPool(host='open.feishu.cn', port=443): "
            f"Max retries exceeded with url: /open-apis/bot/v2/hook/{token} "
            f"(Caused by ConnectTimeoutError(<connection timeout>))"
        )

        with patch.dict(os.environ, {
            "FEISHU_WEBHOOK_URL": f"https://open.feishu.cn/open-apis/bot/v2/hook/{token}",
            "FEISHU_WEBHOOK_ENABLED": "true",
        }):
            with patch(
                "api.services.feishu_webhook_service.send_message",
                side_effect=ConnectionError(leaky_msg),
            ):
                from api.services.notification_confirmation_service import _do_send
                result = _do_send(db_session, entry)
                assert result["success"] is False

                # The raw token must never reach the DB or the response.
                db_session.refresh(entry)
                assert token not in (entry.error or "")
                assert token not in (result.get("error") or "")
                # Error persistence uses full masking, not a token prefix.
                assert "abcd" not in entry.error

    def test_m010_r1_exception_plain_message_preserved(self, db_session):
        """Non-sensitive exception text (e.g. 'timeout') is preserved."""
        entry = NotificationLogDB(
            id="test-send-r1-plain",
            user_id="user-1",
            channel="feishu",
            event_type="test",
            priority="P1",
            title="test",
            status="confirmed",
        )
        db_session.add(entry)
        db_session.commit()

        with patch.dict(os.environ, {
            "FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/abc",
            "FEISHU_WEBHOOK_ENABLED": "true",
        }):
            with patch(
                "api.services.feishu_webhook_service.send_message",
                side_effect=ConnectionError("connection timeout"),
            ):
                from api.services.notification_confirmation_service import _do_send
                result = _do_send(db_session, entry)
                assert result["success"] is False
                assert "timeout" in result["error"]


class TestM010R1ChannelValidation:
    def test_generate_pending_rejects_non_feishu_before_writing(self, db_session):
        from api.services.notification_confirmation_service import generate_pending

        with pytest.raises(ValueError, match="不支持的通知渠道"):
            generate_pending(db_session, "user-1", channel="bark")

        assert db_session.query(NotificationLogDB).count() == 0


# ---------------------------------------------------------------------------
# _log_to_dict tests
# ---------------------------------------------------------------------------


class TestLogToDict:
    def test_dict_structure(self, db_session):
        entry = NotificationLogDB(
            id="test-dict-1",
            user_id="user-1",
            channel="feishu",
            event_type="holdings_risk",
            priority="P0",
            symbol="600519.SH",
            name="贵州茅台",
            title="测试标题",
            reason="测试原因",
            status="pending_confirmation",
        )
        db_session.add(entry)
        db_session.commit()

        from api.services.notification_confirmation_service import _log_to_dict
        d = _log_to_dict(entry)

        assert d["id"] == "test-dict-1"
        assert d["channel"] == "feishu"
        assert d["event_type"] == "holdings_risk"
        assert d["priority"] == "P0"
        assert d["symbol"] == "600519.SH"
        assert d["status"] == "pending_confirmation"
        assert d["created_at"] is not None
        # No webhook URL exposed
        assert "webhook_url" not in d or d.get("webhook_url_masked") is None


# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------


class TestStatusConstants:
    def test_valid_transitions(self):
        from api.services.notification_confirmation_service import (
            VALID_TRANSITIONS,
            STATUS_PENDING,
            STATUS_CONFIRMED,
            STATUS_SENDING,
            STATUS_SENT,
            STATUS_FAILED,
            STATUS_DISMISSED,
        )

        assert STATUS_CONFIRMED in VALID_TRANSITIONS[STATUS_PENDING]
        assert STATUS_DISMISSED in VALID_TRANSITIONS[STATUS_PENDING]
        assert STATUS_SENDING in VALID_TRANSITIONS[STATUS_CONFIRMED]
        assert STATUS_SENT not in VALID_TRANSITIONS  # terminal
        assert STATUS_DISMISSED not in VALID_TRANSITIONS  # terminal
        assert STATUS_CONFIRMED in VALID_TRANSITIONS[STATUS_FAILED]  # retry


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


class TestExports:
    def test_all_exports(self):
        from api.services.notification_confirmation_service import __all__
        assert "generate_pending" in __all__
        assert "list_pending" in __all__
        assert "list_log" in __all__
        assert "confirm_and_send" in __all__
        assert "dismiss" in __all__
        assert "confirm_all_pending" in __all__
