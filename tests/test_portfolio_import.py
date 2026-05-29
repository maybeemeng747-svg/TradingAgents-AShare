import asyncio
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base, UserDB
from api.services import scheduled_service


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _auth_unique(client: TestClient) -> str:
    from api.database import get_db_ctx, init_db
    from api.services import auth_service

    init_db()
    email = auth_service.normalize_email(f"portfolio-import-{uuid4().hex[:8]}@test.com")
    now = datetime.now(timezone.utc)
    with get_db_ctx() as db:
        user = auth_service.get_user_by_email(db, email)
        if not user:
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
    return auth_service.create_access_token(user)


class TestPortfolioImportService:
    def test_sync_positions_stores_positions(self, db):
        from api.services import portfolio_import_service

        result = portfolio_import_service.sync_positions(
            db=db,
            user_id="user1",
            positions=[
                {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 500, "average_cost": 1700.0, "market_value": 850000.0},
                {"symbol": "300750.SZ", "name": "宁德时代", "current_position": 200, "average_cost": 205.5, "market_value": 41100.0},
            ],
            auto_apply_scheduled=True,
        )

        assert result["summary"]["positions"] == 2
        by_symbol = {item["symbol"]: item for item in result["positions"]}
        assert by_symbol["600519.SH"]["current_position"] == pytest.approx(500.0)
        assert by_symbol["600519.SH"]["average_cost"] == pytest.approx(1700.0)

    def test_sync_positions_auto_creates_scheduled_tasks(self, db):
        from api.services import portfolio_import_service

        portfolio_import_service.sync_positions(
            db=db,
            user_id="user-auto-scheduled",
            positions=[
                {"symbol": "600519.SH", "current_position": 500, "market_value": 850000.0},
                {"symbol": "300750.SZ", "current_position": 200, "market_value": 41100.0},
            ],
            auto_apply_scheduled=True,
        )

        tasks = scheduled_service.list_scheduled(db, "user-auto-scheduled")
        assert [(item["symbol"], item["trigger_time"]) for item in tasks] == [
            ("600519.SH", "14:30"),
            ("600519.SH", "20:00"),
            ("300750.SZ", "14:30"),
            ("300750.SZ", "20:00"),
        ]

    def test_sync_positions_normalizes_bare_codes(self, db):
        from api.services import portfolio_import_service

        result = portfolio_import_service.sync_positions(
            db=db,
            user_id="user-bare",
            positions=[
                {"symbol": "600519", "current_position": 100},
                {"symbol": "000858", "current_position": 200},
            ],
        )

        symbols = [p["symbol"] for p in result["positions"]]
        assert "600519.SH" in symbols
        assert "000858.SZ" in symbols

    def test_sync_positions_does_not_infer_account_pct_from_import_subset(self, db):
        from api.services import portfolio_import_service

        result = portfolio_import_service.sync_positions(
            db=db,
            user_id="user-no-pct-infer",
            positions=[
                {
                    "symbol": "601958.SH",
                    "name": "金钼股份",
                    "current_position": 200,
                    "average_cost": 20.57,
                    "market_value": 4114.0,
                },
            ],
        )

        assert result["positions"][0]["current_position_pct"] is None

    def test_sync_positions_preserves_explicit_account_pct(self, db):
        from api.services import portfolio_import_service

        result = portfolio_import_service.sync_positions(
            db=db,
            user_id="user-explicit-pct",
            positions=[
                {
                    "symbol": "601958.SH",
                    "current_position": 200,
                    "market_value": 4114.0,
                    "current_position_pct": 12.5,
                },
            ],
        )

        assert result["positions"][0]["current_position_pct"] == pytest.approx(12.5)

    def test_sync_positions_deduplicates(self, db):
        from api.services import portfolio_import_service

        result = portfolio_import_service.sync_positions(
            db=db,
            user_id="user-dedup",
            positions=[
                {"symbol": "600519.SH", "current_position": 100},
                {"symbol": "600519.SH", "current_position": 200},
            ],
        )

        assert result["summary"]["positions"] == 1

    def test_clear_imported_portfolio(self, db):
        from api.services import portfolio_import_service

        portfolio_import_service.sync_positions(
            db=db,
            user_id="user-clear",
            positions=[{"symbol": "600519.SH", "current_position": 100}],
        )
        portfolio_import_service.clear_imported_portfolio(db, "user-clear")
        state = portfolio_import_service.get_import_state(db, "user-clear")
        assert state["summary"]["positions"] == 0

    def test_scheduled_job_uses_imported_position_context(self, db):
        from scheduler.main import _run_scheduled_job
        from api.services import portfolio_import_service

        portfolio_import_service.sync_positions(
            db=db,
            user_id="user1",
            positions=[
                {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 500, "average_cost": 1700.0, "market_value": 850000.0},
            ],
            auto_apply_scheduled=True,
        )
        task = next(item for item in scheduled_service.list_scheduled(db, "user1") if item["symbol"] == "600519.SH")

        captured = {}

        async def fake_run_job(job_id, request, *args, **kwargs):
            captured["request"] = request

        class FakeDbCtx:
            def __enter__(self):
                return db

            def __exit__(self, exc_type, exc_val, exc_tb):
                if exc_type is not None:
                    db.rollback()

        with patch("scheduler.main._run_job", side_effect=fake_run_job), patch(
            "scheduler.main.get_db_ctx",
            return_value=FakeDbCtx(),
        ), patch("tradingagents.dataflows.trade_calendar.is_cn_trading_day", return_value=True):
            asyncio.run(
                _run_scheduled_job(
                    {
                        "id": task["id"],
                        "user_id": "user1",
                        "symbol": "600519.SH",
                        "horizon": "short",
                    },
                    "2026-03-30",
                )
            )

        request = captured["request"]
        assert request.current_position == pytest.approx(500.0)
        assert request.average_cost == pytest.approx(1700.0)
        assert "持仓导入" in (request.user_notes or "")

    def test_imported_context_warns_when_account_pct_unknown(self, db):
        from api.services import portfolio_import_service

        portfolio_import_service.sync_positions(
            db=db,
            user_id="user-unknown-account-pct",
            positions=[
                {
                    "symbol": "601958.SH",
                    "name": "金钼股份",
                    "current_position": 200,
                    "average_cost": 20.57,
                    "market_value": 4114.0,
                },
            ],
            auto_apply_scheduled=False,
        )

        context = portfolio_import_service.build_scheduled_user_context(
            db, "user-unknown-account-pct", "601958.SH"
        )

        assert "current_position_pct" not in context
        assert "禁止据此推断满仓" in context["user_notes"]

    def test_scheduled_job_marks_failed_when_underlying_job_fails(self, db):
        from scheduler.main import _run_scheduled_job, _set_job

        item = scheduled_service.create_scheduled(db, "user-failed", "300750.SZ", "short")

        class FakeDbCtx:
            def __enter__(self):
                return db

            def __exit__(self, exc_type, exc_val, exc_tb):
                if exc_type is not None:
                    db.rollback()

        async def fake_run_job(job_id, request, *args, **kwargs):
            _set_job(job_id, status="failed", error="ModuleNotFoundError: missing module")

        with patch("scheduler.main._run_job", side_effect=fake_run_job), patch(
            "scheduler.main.get_db_ctx",
            return_value=FakeDbCtx(),
        ), patch("tradingagents.dataflows.trade_calendar.is_cn_trading_day", return_value=True):
            asyncio.run(
                _run_scheduled_job(
                    {
                        "id": item["id"],
                        "user_id": "user-failed",
                        "symbol": "300750.SZ",
                        "horizon": "short",
                    },
                    "2026-03-30",
                )
            )

        scheduled = scheduled_service.get_scheduled(db, "user-failed", item["id"])
        assert scheduled["last_run_status"] == "failed"
        assert scheduled["consecutive_failures"] == 1


class TestPortfolioImportApi:
    def test_repair_position_symbols_prefers_name_when_ocr_code_disagrees(self, monkeypatch):
        import api.main as main

        monkeypatch.setattr(main, "_load_cn_stock_map", lambda: {"金钼股份": "601958.SH"})
        monkeypatch.setattr(main, "_get_reverse_stock_map", lambda: {"601958.SH": "金钼股份", "600519.SH": "贵州茅台"})

        result = main._repair_portfolio_position_symbols([
            {"symbol": "600519", "name": "金钼股份", "current_position": 200, "average_cost": 20.57}
        ])

        assert result[0]["symbol"] == "601958.SH"
        assert result[0]["symbol_correction"]["from"] == "600519.SH"

    def test_sync_endpoint_stores_positions(self):
        from api.main import app

        client = TestClient(app, raise_server_exceptions=False)
        token = _auth_unique(client)
        headers = {"Authorization": f"Bearer {token}"}

        response = client.post(
            "/v1/portfolio/imports",
            headers=headers,
            json={
                "positions": [
                    {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 500, "average_cost": 1700.0},
                    {"symbol": "300750.SZ", "name": "宁德时代", "current_position": 200, "average_cost": 205.5},
                ],
                "auto_apply_scheduled": True,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["summary"]["positions"] == 2
        assert any(item["symbol"] == "600519.SH" for item in body["positions"])

        scheduled = client.get("/v1/scheduled", headers=headers)
        assert scheduled.status_code == 200
        scheduled_pairs = [(item["symbol"], item["trigger_time"]) for item in scheduled.json()["items"]]
        assert scheduled_pairs == [
            ("600519.SH", "14:30"),
            ("600519.SH", "20:00"),
            ("300750.SZ", "14:30"),
            ("300750.SZ", "20:00"),
        ]
