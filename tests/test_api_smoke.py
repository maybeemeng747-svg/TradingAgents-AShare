"""API smoke tests using FastAPI TestClient (no external server needed).

Covers:
1. AnalyzeRequest schema — query field exists, symbol optional
2. /v1/analyze dry_run — legacy single-horizon path works
3. /v1/analyze with query field — schema accepts it, dry_run still short-circuits
4. /v1/chat/completions — unrecognizable stock returns 400
5. /v1/chat/completions — valid stock dry_run completes job
6. /v1/jobs/{id}/result — completed job returns result
"""
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.database import ImportedPortfolioPositionDB, UserTokenDB, get_db_ctx
from api.services import token_service


# ---------------------------------------------------------------------------
# Schema-only test (no server needed)
# ---------------------------------------------------------------------------

class TestAnalyzeRequestSchema:
    def test_query_field_exists_and_optional(self):
        from api.main import AnalyzeRequest
        # query defaults to None
        req = AnalyzeRequest(symbol="600519.SH")
        assert req.query is None

    def test_query_field_accepts_string(self):
        from api.main import AnalyzeRequest
        req = AnalyzeRequest(symbol="600519.SH", query="分析贵州茅台短线机会")
        assert req.query == "分析贵州茅台短线机会"

    def test_symbol_is_optional(self):
        from api.main import AnalyzeRequest
        # should not raise
        req = AnalyzeRequest()
        assert req.symbol == ""

    def test_dry_run_defaults_false(self):
        from api.main import AnalyzeRequest
        req = AnalyzeRequest(symbol="600519.SH")
        assert req.dry_run is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_client():
    """Create a TestClient for the FastAPI app."""
    from api.main import app
    return TestClient(app, raise_server_exceptions=False)


def _auth(client: TestClient) -> str:
    """Register a test user and return a valid JWT token."""
    r = client.post("/v1/auth/request-code", json={"email": "apitest@test.com"})
    code = r.json()["dev_code"]
    r2 = client.post("/v1/auth/verify-code", json={"email": "apitest@test.com", "code": code})
    return r2.json()["access_token"]


def _auth_unique(client: TestClient) -> str:
    from api.database import UserDB, get_db_ctx, init_db
    from api.services import auth_service

    init_db()
    email = auth_service.normalize_email(f"apitest-{uuid4().hex[:8]}@test.com")
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


def _wait_job(client: TestClient, token: str, job_id: str, timeout: float = 5.0) -> dict:
    """Poll until job is no longer running, return result dict."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/v1/jobs/{job_id}", headers={"Authorization": f"Bearer {token}"})
        status = r.json().get("status")
        if status in ("completed", "failed"):
            break
        time.sleep(0.2)
    r2 = client.get(f"/v1/jobs/{job_id}/result", headers={"Authorization": f"Bearer {token}"})
    return r2.json()


# ---------------------------------------------------------------------------
# API integration tests
# ---------------------------------------------------------------------------

class TestAnalyzeEndpoint:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = _get_client()
        self.token = _auth_unique(self.client)
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def test_dry_run_completes(self):
        """Legacy path: symbol + dry_run → completed immediately."""
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "symbol": "600519.SH",
            "trade_date": "2024-01-15",
            "dry_run": True,
        })
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        result = _wait_job(self.client, self.token, job_id)
        assert result["status"] == "completed"
        assert result["decision"] == "DRY_RUN"
        assert result["result"]["symbol"] == "600519.SH"

    def test_query_field_accepted_with_dry_run(self):
        """query field is accepted by schema; dry_run still short-circuits before LLM."""
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "symbol": "600519.SH",
            "trade_date": "2024-01-15",
            "query": "分析贵州茅台短线机会，关注量价关系",
            "dry_run": True,
        })
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        result = _wait_job(self.client, self.token, job_id)
        assert result["status"] == "completed"
        assert result["decision"] == "DRY_RUN"

    def test_query_date_is_honored_when_symbol_field_is_present(self):
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "symbol": "AAPL",
            "query": "请分析 AAPL 在 2023-01-01 的表现",
            "dry_run": True,
        })
        assert r.status_code == 200
        result = _wait_job(self.client, self.token, r.json()["job_id"])
        assert result["status"] == "completed"
        assert result["result"]["trade_date"] == "2023-01-01"

    def test_conflicting_query_and_request_symbols_are_rejected(self):
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "symbol": "600519.SH",
            "query": "请分析 000001.SZ 的短线机会",
            "trade_date": "2024-01-15",
            "dry_run": True,
        })

        assert r.status_code == 422
        assert "分析标的不一致" in r.json()["detail"]

    def test_invalid_query_date_is_rejected_before_job_creation(self):
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "symbol": "600519.SH",
            "query": "请分析 600519.SH 在 2026-02-30 的表现",
            "dry_run": True,
        })
        assert r.status_code == 422
        assert "分析日期无效" in r.json()["detail"]

    def test_overlong_hk_symbol_is_rejected_instead_of_redirected(self):
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "symbol": "123456.HK",
            "trade_date": "2024-01-15",
            "dry_run": True,
        })
        assert r.status_code == 422
        assert "分析标的格式无效" in r.json()["detail"]

    def test_mismatched_cn_exchange_suffix_is_rejected(self):
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "symbol": "600519.BJ",
            "trade_date": "2024-01-15",
            "dry_run": True,
        })
        assert r.status_code == 422
        assert "分析标的格式无效" in r.json()["detail"]

    def test_generic_tickers_are_not_truncated_by_analysis_normalization(self):
        for symbol in ("RDS-A", "BRK-B", "A1"):
            r = self.client.post("/v1/analyze", headers=self.headers, json={
                "symbol": symbol,
                "trade_date": "2024-01-15",
                "dry_run": True,
            })
            assert r.status_code == 200
            result = _wait_job(self.client, self.token, r.json()["job_id"])
            assert result["status"] == "completed"
            assert result["result"]["symbol"] == symbol

    def test_unsupported_crypto_pair_is_rejected_without_ticker_truncation(self):
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "symbol": "BTC-USD",
            "trade_date": "2024-01-15",
            "dry_run": True,
        })
        assert r.status_code == 422
        assert "分析标的格式无效" in r.json()["detail"]

    def test_supported_cn_indices_pass_exchange_validation(self):
        for symbol in ("000001.SH", "000300.SH", "000688.SH"):
            r = self.client.post("/v1/analyze", headers=self.headers, json={
                "symbol": symbol,
                "trade_date": "2024-01-15",
                "dry_run": True,
            })
            assert r.status_code == 200
            result = _wait_job(self.client, self.token, r.json()["job_id"])
            assert result["status"] == "completed"
            assert result["result"]["symbol"] == symbol

    def test_short_exchange_qualified_codes_are_rejected(self):
        for symbol in ("5.SH", "123.SZ", "12.BJ"):
            r = self.client.post("/v1/analyze", headers=self.headers, json={
                "symbol": symbol,
                "trade_date": "2024-01-15",
                "dry_run": True,
            })
            assert r.status_code == 422
            assert "分析标的格式无效" in r.json()["detail"]

    def test_ticker_first_query_without_symbol_preserves_the_instrument(self):
        for query, symbol in (
            ("AAPL is rising, should I buy?", "AAPL"),
            ("RDS-A looks attractive", "RDS-A"),
        ):
            r = self.client.post("/v1/analyze", headers=self.headers, json={
                "query": query,
                "trade_date": "2024-01-15",
                "dry_run": True,
            })
            assert r.status_code == 200
            result = _wait_job(self.client, self.token, r.json()["job_id"])
            assert result["status"] == "completed"
            assert result["result"]["symbol"] == symbol

    def test_missing_symbol_and_query_are_rejected_before_job_creation(self):
        """An unresolved request must not enqueue provider or LLM work."""
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "trade_date": "2024-01-15",
            "dry_run": True,
        })
        assert r.status_code == 422
        assert "无法唯一识别分析标的" in r.json()["detail"]

    def test_requires_auth(self):
        """Unauthenticated request returns 401/403."""
        r = self.client.post("/v1/analyze", json={
            "symbol": "600519.SH", "dry_run": True,
        })
        assert r.status_code in (401, 403)

    def test_selected_analysts_field(self):
        """selected_analysts are echoed back in dry_run result."""
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "symbol": "600519.SH",
            "selected_analysts": ["market", "news"],
            "dry_run": True,
        })
        job_id = r.json()["job_id"]
        result = _wait_job(self.client, self.token, job_id)
        assert result["result"]["selected_analysts"] == ["market", "news"]

    def test_dry_run_merges_imported_position_context_for_manual_analysis(self):
        current_user = self.client.get("/v1/auth/me", headers=self.headers).json()
        now = datetime.now(timezone.utc)

        with get_db_ctx() as db:
            db.query(ImportedPortfolioPositionDB).filter(
                ImportedPortfolioPositionDB.user_id == current_user["id"],
                ImportedPortfolioPositionDB.source == "manual",
                ImportedPortfolioPositionDB.symbol == "600519.SH",
            ).delete()
            db.add(
                ImportedPortfolioPositionDB(
                    id=uuid4().hex,
                    user_id=current_user["id"],
                    source="manual",
                    symbol="600519.SH",
                    security_name="贵州茅台",
                    current_position=300.0,
                    average_cost=1680.5,
                    market_value=504150.0,
                    current_position_pct=42.5,
                    trade_points_json=[],
                    trade_points_count=0,
                    last_imported_at=now,
                )
            )
            db.commit()

        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "symbol": "600519.SH",
            "trade_date": "2024-01-15",
            "dry_run": True,
        })
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        result = _wait_job(self.client, self.token, job_id)

        user_context = result["result"]["user_context"]
        assert user_context["current_position"] == pytest.approx(300.0)
        assert user_context["average_cost"] == pytest.approx(1680.5)
        assert user_context["current_position_pct"] == pytest.approx(42.5)
        assert "持仓导入" in (user_context.get("user_notes") or "")


class TestChatCompletionsEndpoint:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = _get_client()
        self.token = _auth(self.client)
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def test_unrecognizable_stock_returns_error(self):
        """Non-stock text returns 400 with Chinese error message."""
        # Mock the LLM used for stock extraction to return no stock
        with patch("api.main._ai_extract_symbol_and_date", return_value=(None, None, ["short"], [], [], {})):
            r = self.client.post("/v1/chat/completions", headers=self.headers, json={
                "messages": [{"role": "user", "content": "今天天气真好"}],
                "stream": False,
                "dry_run": True,
            })
        assert r.status_code == 400

    def test_invalid_date_is_rejected_before_nonstream_job_creation(self):
        with (
            patch(
                "api.main._ai_extract_symbol_and_date",
                return_value=("AAPL", "2026-02-30", ["short"], [], [], {}),
            ),
            patch("api.main._run_job") as run_job,
        ):
            response = self.client.post(
                "/v1/chat/completions",
                headers=self.headers,
                json={
                    "messages": [
                        {"role": "user", "content": "分析 AAPL 2026-02-30"}
                    ],
                    "stream": False,
                    "dry_run": True,
                },
            )

        assert response.status_code == 422
        assert "分析日期无效" in response.json()["detail"]
        run_job.assert_not_called()

    def test_invalid_date_fails_stream_before_job_creation(self):
        async def _extract_invalid_date(*_args, **_kwargs):
            return "AAPL", "2026-02-30", ["short"], [], [], {}

        with (
            patch(
                "api.main._ai_extract_symbol_and_date_streaming",
                side_effect=_extract_invalid_date,
            ),
            patch("api.main._run_job") as run_job,
        ):
            with self.client.stream(
                "POST",
                "/v1/chat/completions",
                headers=self.headers,
                json={
                    "messages": [
                        {"role": "user", "content": "分析 AAPL 2026-02-30"}
                    ],
                    "stream": True,
                    "dry_run": True,
                },
            ) as response:
                body = "".join(response.iter_text())

        assert response.status_code == 200
        assert "分析日期无效" in body
        run_job.assert_not_called()

    def test_valid_stock_dry_run_creates_job(self):
        """Valid stock message with dry_run creates and completes a job."""
        with patch("api.main._ai_extract_symbol_and_date", return_value=("600519.SH", "2024-01-15", ["short"], [], [], {})):
            r = self.client.post("/v1/chat/completions", headers=self.headers, json={
                "messages": [{"role": "user", "content": "分析600519短线机会"}],
                "stream": False,
                "dry_run": True,
            })
        assert r.status_code == 200
        body = r.json()
        # Non-stream returns OpenAI-compatible format with job_id embedded in content
        assert "choices" in body
        content = body["choices"][0]["message"]["content"]
        # Extract job_id from content (format: "已启动分析任务：<job_id>")
        job_id = body["id"].replace("chatcmpl-", "")
        result = _wait_job(self.client, self.token, job_id)
        assert result["status"] == "completed"
        assert result["decision"] == "DRY_RUN"

    def test_chat_rejects_mismatched_cn_exchange_suffix(self):
        with patch(
            "api.main._ai_extract_symbol_and_date",
            return_value=("600519.BJ", "2024-01-15", ["short"], [], [], {}),
        ):
            r = self.client.post("/v1/chat/completions", headers=self.headers, json={
                "messages": [{"role": "user", "content": "分析600519.BJ"}],
                "stream": False,
                "dry_run": True,
            })
        assert r.status_code == 400
        assert "交易所后缀无效" in r.json()["detail"]

    def test_chat_rejects_multi_symbol_before_llm_can_choose_one(self):
        for query in (
            "Compare AAPL and MSFT",
            "analyze aapl and msft",
            "please analyze aapl or msft",
            "分析600519和BRK.B",
            "比较AAPL和600519",
            "分析 AAPL、MSFT",
            "AAPL/MSFT",
        ):
            with patch(
                "api.main._ai_extract_symbol_and_date",
                return_value=("AAPL", None, ["short"], [], [], {}),
            ) as extractor:
                r = self.client.post("/v1/chat/completions", headers=self.headers, json={
                    "messages": [{"role": "user", "content": query}],
                    "stream": False,
                    "dry_run": True,
                })

            assert r.status_code == 422
            assert "一个明确标的" in r.json()["detail"]
            extractor.assert_not_called()

    def test_chat_imported_zero_position_is_not_marked_explicit(self):
        captured = {}

        async def _capture_run(_job_id, analyze_request, *_args, **_kwargs):
            captured["request"] = analyze_request

        with (
            patch(
                "api.main._ai_extract_symbol_and_date",
                return_value=("AAPL", None, ["short"], [], [], {}),
            ),
            patch(
                "api.main._compose_analysis_user_context",
                return_value={"current_position": 0},
            ),
            patch("api.main._run_job", side_effect=_capture_run),
        ):
            r = self.client.post("/v1/chat/completions", headers=self.headers, json={
                "messages": [{"role": "user", "content": "我有持仓，请帮我减仓 AAPL"}],
                "stream": False,
                "dry_run": True,
            })

        assert r.status_code == 200
        analyze_request = captured["request"]
        assert "current_position" not in analyze_request.model_fields_set
        assert analyze_request.current_position is None
        assert analyze_request.user_intent["user_context"]["current_position"] == 0

    def test_chat_dry_run_reports_imported_and_inferred_context(self):
        merged_context = {
            "current_position": 200,
            "average_cost": 123.45,
            "current_position_pct": 25,
            "user_notes": "持仓导入；模型补充",
        }
        with (
            patch(
                "api.main._ai_extract_symbol_and_date",
                return_value=(
                    "AAPL",
                    None,
                    ["short"],
                    [],
                    [],
                    {"user_notes": "模型补充"},
                ),
            ),
            patch(
                "api.main._compose_analysis_user_context",
                return_value=merged_context,
            ),
        ):
            response = self.client.post(
                "/v1/chat/completions",
                headers=self.headers,
                json={
                    "messages": [{"role": "user", "content": "分析 AAPL"}],
                    "stream": False,
                    "dry_run": True,
                },
            )

        assert response.status_code == 200
        job_id = response.json()["id"].replace("chatcmpl-", "")
        result = _wait_job(self.client, self.token, job_id)
        assert result["result"]["user_context"] == merged_context

    def test_streaming_chat_rejects_multi_symbol_before_job_creation(self):
        r = self.client.post("/v1/chat/completions", headers=self.headers, json={
            "messages": [{"role": "user", "content": "比较 AAPL 和 MSFT"}],
            "stream": True,
            "dry_run": True,
        })

        assert r.status_code == 422
        assert "一个明确标的" in r.json()["detail"]

    def test_requires_auth(self):
        r = self.client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "分析600519"}],
            "stream": False,
        })
        assert r.status_code in (401, 403)


class TestOpenAPISchema:
    def test_analyze_request_has_query_field(self):
        client = _get_client()
        r = client.get("/openapi.json")
        assert r.status_code == 200
        schema = r.json()["components"]["schemas"]["AnalyzeRequest"]
        assert "query" in schema["properties"]

    def test_analyze_request_symbol_not_required(self):
        client = _get_client()
        r = client.get("/openapi.json")
        schema = r.json()["components"]["schemas"]["AnalyzeRequest"]
        assert "symbol" not in schema.get("required", [])

    def test_healthz(self):
        client = _get_client()
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_model_catalog_requires_auth(self):
        client = _get_client()
        r = client.get("/v1/config/model-catalog")
        assert r.status_code in (401, 403)

    def test_db_hygiene_requires_auth(self):
        client = _get_client()
        r = client.get("/v1/db-hygiene")
        assert r.status_code in (401, 403)

    def test_db_hygiene_allows_api_user(self):
        client = _get_client()
        token = _auth_unique(client)
        r = client.get("/v1/db-hygiene", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert "all_green" in r.json()

    def test_db_hygiene_api_token_does_not_update_last_used_at(self):
        client = _get_client()
        jwt = _auth_unique(client)
        from api.services import auth_service

        user_id = str(auth_service.decode_access_token(jwt)["sub"])
        with get_db_ctx() as db:
            created = token_service.create_token(db, user_id, "db-hygiene-readonly")
            token_id = created["id"]
            api_token = created["token"]
            token_row = db.query(UserTokenDB).filter(UserTokenDB.id == token_id).first()
            assert token_row is not None
            assert token_row.last_used_at is None

        r = client.get("/v1/db-hygiene", headers={"Authorization": f"Bearer {api_token}"})
        assert r.status_code == 200

        with get_db_ctx() as db:
            token_row = db.query(UserTokenDB).filter(UserTokenDB.id == token_id).first()
            assert token_row is not None
            assert token_row.last_used_at is None


class TestRuntimeConfigWarmup:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = _get_client()
        self.token = _auth_unique(self.client)
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def test_model_change_schedules_warmup(self):
        model_name = f"gpt-test-quick-{uuid4().hex[:8]}"
        with patch("api.main._probe_runtime_config", return_value={"status": "ok", "model": model_name}) as probe, \
             patch("api.main._run_config_warmup") as warmup:
            r = self.client.patch("/v1/config", headers=self.headers, json={
                "quick_think_llm": model_name,
            })
        assert r.status_code == 200
        body = r.json()
        assert body["warmup"]["status"] == "scheduled"
        assert body["warmup"]["triggered"] is True
        assert model_name in body["warmup"]["models"]
        warmup.assert_called_once()

    def test_model_catalog_returns_supported_endpoints(self):
        r = self.client.get("/v1/config/model-catalog", headers=self.headers)
        assert r.status_code == 200
        body = r.json()
        items = {item["id"]: item for item in body["items"]}
        assert "zhipu-coding" in items
        assert items["zhipu-coding"]["base_url"] == "https://open.bigmodel.cn/api/coding/paas/v4"
        assert items["zhipu-coding"]["deep_model"] == "glm-5-turbo"
        assert "deepseek" in items

    def test_non_model_change_skips_warmup(self):
        with patch("api.main._run_config_warmup") as warmup:
            r = self.client.patch("/v1/config", headers=self.headers, json={
                "max_debate_rounds": 3,
            })
        assert r.status_code == 200
        body = r.json()
        assert body["warmup"]["status"] == "skipped"
        assert body["warmup"]["triggered"] is False
        warmup.assert_not_called()

    def test_api_key_is_probed_before_save(self):
        with patch("api.main._probe_runtime_config", return_value={"status": "ok", "model": "moonshot-v1-8k"}) as probe, \
             patch("api.main._run_config_warmup") as warmup:
            r = self.client.patch("/v1/config", headers=self.headers, json={
                "llm_provider": "openai",
                "backend_url": "https://api.moonshot.cn/v1",
                "quick_think_llm": "moonshot-v1-8k",
                "api_key": "sk-test-valid",
            })
        assert r.status_code == 200
        probe.assert_called_once()
        warmup.assert_called_once()

    def test_invalid_api_key_is_rejected_before_save(self):
        with patch("api.main._probe_runtime_config", side_effect=HTTPException(status_code=400, detail="模型 Key 验证失败")) as probe, \
             patch("api.main._run_config_warmup") as warmup:
            r = self.client.patch("/v1/config", headers=self.headers, json={
                "llm_provider": "openai",
                "backend_url": "https://api.moonshot.cn/v1",
                "quick_think_llm": "moonshot-v1-8k",
                "api_key": "sk-test-invalid",
            })
        assert r.status_code == 400
        assert "模型 Key 验证失败" in r.json()["detail"]
        probe.assert_called_once()
        warmup.assert_not_called()

    def test_model_endpoint_change_uses_matching_scoped_key(self):
        with patch("api.main._probe_runtime_config", return_value={"status": "ok", "model": "moonshot-v1-8k"}), \
             patch("api.main._run_config_warmup"):
            r = self.client.patch("/v1/config", headers=self.headers, json={
                "llm_provider": "openai",
                "backend_url": "https://api.moonshot.cn/v1",
                "quick_think_llm": "moonshot-v1-8k",
                "api_key": "sk-stored-valid",
            })
        assert r.status_code == 200

        from api.database import get_db_ctx
        from api.services import auth_service

        user_id = auth_service.decode_access_token(self.token)["sub"]
        with get_db_ctx() as db:
            auth_service.upsert_user_provider_api_key(
                db,
                user_id,
                "openai",
                "https://open.bigmodel.cn/api/coding/paas/v4",
                "zhipu-stored-valid",
            )

        with patch("api.main._probe_runtime_config", return_value={"status": "ok", "model": "glm-4.5-air"}) as probe, \
             patch("api.main._run_config_warmup"):
            r = self.client.patch("/v1/config", headers=self.headers, json={
                "backend_url": "https://open.bigmodel.cn/api/coding/paas/v4",
                "quick_think_llm": "glm-4.5-air",
                "deep_think_llm": "glm-5-turbo",
            })

        assert r.status_code == 200
        probe.assert_called_once()
        assert probe.call_args.args[0]["api_key"] == "zhipu-stored-valid"

    def test_force_warmup_schedules_even_without_model_change(self):
        with patch("api.main._run_config_warmup") as warmup:
            r = self.client.patch("/v1/config", headers=self.headers, json={
                "max_debate_rounds": 3,
                "force_warmup": True,
            })
        assert r.status_code == 200
        body = r.json()
        assert body["warmup"]["status"] == "scheduled"
        assert body["warmup"]["triggered"] is True
        warmup.assert_called_once()

    def test_manual_warmup_returns_model_reply(self):
        with patch("api.main._invoke_runtime_warmup", return_value=[{
            "model": "gpt-test-quick",
            "targets": ["常规模型"],
            "content": "你好，我已准备就绪。",
            "error": None,
        }]) as invoke:
            r = self.client.post("/v1/config/warmup", headers=self.headers, json={
                "quick_think_llm": "gpt-test-quick",
                "prompt": "你好",
            })

        assert r.status_code == 200
        body = r.json()
        assert body["prompt"] == "你好"
        assert body["results"][0]["content"] == "你好，我已准备就绪。"
        invoke.assert_called_once()
        assert invoke.call_args.args[0]["quick_think_llm"] == "gpt-test-quick"
        assert invoke.call_args.args[1] == "你好"

    def test_manual_warmup_surfaces_upstream_error(self):
        with patch(
            "api.main._invoke_runtime_warmup",
            side_effect=HTTPException(status_code=400, detail="模型 warmup 失败：upstream timeout"),
        ):
            r = self.client.post("/v1/config/warmup", headers=self.headers, json={
                "quick_think_llm": "gpt-test-quick",
                "prompt": "你好",
            })

        assert r.status_code == 400
        assert "模型 warmup 失败" in r.json()["detail"]


class TestWecomRuntimeConfig:
    WEBHOOK_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=e1d21302-1925-4247-ad5a-6bc023c7fd2a"

    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = _get_client()
        self.token = _auth_unique(self.client)
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def test_config_returns_masked_webhook_and_toggle_state(self):
        r = self.client.patch("/v1/config", headers=self.headers, json={
            "wecom_webhook_url": self.WEBHOOK_URL,
            "wecom_report_enabled": False,
            "warmup": False,
        })

        assert r.status_code == 200
        body = r.json()
        current = body["current"]
        assert current["has_wecom_webhook"] is True
        assert current["wecom_report_enabled"] is False
        assert current["wecom_webhook_display"].startswith("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=")
        assert current["wecom_webhook_display"] != self.WEBHOOK_URL
        assert "fd2a" in current["wecom_webhook_display"]
        assert body["applied"]["wecom_report_enabled"] is False

        config_resp = self.client.get("/v1/config", headers=self.headers)
        assert config_resp.status_code == 200
        assert config_resp.json()["wecom_report_enabled"] is False

    def test_wecom_warmup_uses_stored_webhook_when_input_missing(self):
        save_resp = self.client.patch("/v1/config", headers=self.headers, json={
            "wecom_webhook_url": self.WEBHOOK_URL,
            "warmup": False,
        })
        assert save_resp.status_code == 200

        with patch("api.services.wecom_notification_service.send_message", return_value=True) as mock_send:
            r = self.client.post("/v1/config/wecom/warmup", headers=self.headers, json={})

        assert r.status_code == 200
        body = r.json()
        assert body["sent"] is True
        assert "成功" in body["message"]
        assert mock_send.call_count == 1
        assert "TradingAgents Webhook Warmup" in mock_send.call_args.args[0]
        assert mock_send.call_args.args[1] == self.WEBHOOK_URL

    def test_inline_wecom_warmup_does_not_persist_unsaved_webhook(self):
        with patch("api.services.wecom_notification_service.send_message", return_value=True) as mock_send:
            r = self.client.post("/v1/config/wecom/warmup", headers=self.headers, json={
                "wecom_webhook_url": "inline-key-1234",
            })

        assert r.status_code == 200
        assert mock_send.call_count == 1
        assert mock_send.call_args.args[1] == (
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=inline-key-1234"
        )

        config_resp = self.client.get("/v1/config", headers=self.headers)
        assert config_resp.status_code == 200
        assert config_resp.json()["has_wecom_webhook"] is False

    def test_invalid_wecom_url_is_rejected(self):
        r = self.client.patch("/v1/config", headers=self.headers, json={
            "wecom_webhook_url": "http://169.254.169.254/latest/meta-data/",
            "warmup": False,
        })

        assert r.status_code == 400
        assert "企业微信 Webhook" in r.json()["detail"]


class TestBarkRuntimeConfig:
    BARK_KEY = "abcd1234efgh5678"
    BARK_URL = f"https://api.day.app/{BARK_KEY}"

    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = _get_client()
        self.token = _auth_unique(self.client)
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def test_config_returns_masked_bark_url_and_toggle_state(self):
        r = self.client.patch("/v1/config", headers=self.headers, json={
            "bark_url": self.BARK_KEY,
            "bark_report_enabled": False,
            "warmup": False,
        })

        assert r.status_code == 200
        body = r.json()
        current = body["current"]
        assert current["has_bark_url"] is True
        assert current["bark_report_enabled"] is False
        assert current["bark_url_display"].startswith("https://api.day.app/")
        assert current["bark_url_display"] != self.BARK_URL
        assert "abcd" in current["bark_url_display"]
        assert "5678" in current["bark_url_display"]
        assert body["applied"]["bark_report_enabled"] is False

        config_resp = self.client.get("/v1/config", headers=self.headers)
        assert config_resp.status_code == 200
        assert config_resp.json()["bark_report_enabled"] is False

    def test_bark_warmup_uses_stored_url_when_input_missing(self):
        save_resp = self.client.patch("/v1/config", headers=self.headers, json={
            "bark_url": self.BARK_URL,
            "warmup": False,
        })
        assert save_resp.status_code == 200

        with patch("api.services.bark_notification_service.send_message", return_value=True) as mock_send:
            r = self.client.post("/v1/config/bark/warmup", headers=self.headers, json={})

        assert r.status_code == 200
        body = r.json()
        assert body["sent"] is True
        assert "成功" in body["message"]
        assert mock_send.call_count == 1
        assert mock_send.call_args.args[0]["title"] == "TradingAgents Bark 测试"
        assert mock_send.call_args.args[1] == self.BARK_URL

    def test_inline_bark_warmup_does_not_persist_unsaved_url(self):
        with patch("api.services.bark_notification_service.send_message", return_value=True) as mock_send:
            r = self.client.post("/v1/config/bark/warmup", headers=self.headers, json={
                "bark_url": self.BARK_KEY,
            })

        assert r.status_code == 200
        assert mock_send.call_count == 1
        assert mock_send.call_args.args[1] == self.BARK_URL

        config_resp = self.client.get("/v1/config", headers=self.headers)
        assert config_resp.status_code == 200
        assert config_resp.json()["has_bark_url"] is False

    def test_invalid_bark_url_is_rejected(self):
        r = self.client.patch("/v1/config", headers=self.headers, json={
            "bark_url": "http://api.day.app/unsafe",
            "warmup": False,
        })

        assert r.status_code == 400
        assert "Bark 地址" in r.json()["detail"]

    def test_report_create_sends_bark_when_enabled(self):
        save_resp = self.client.patch("/v1/config", headers=self.headers, json={
            "bark_url": self.BARK_URL,
            "bark_report_enabled": True,
            "warmup": False,
        })
        assert save_resp.status_code == 200

        with patch("api.services.bark_notification_service.send_report_message_with_retry", new_callable=AsyncMock) as mock_send:
            r = self.client.post("/v1/reports", headers=self.headers, json={
                "symbol": "601958.SH",
                "trade_date": "2026-05-07",
                "decision": "BUY",
                "result_data": {
                    "symbol": "601958.SH",
                    "trade_date": "2026-05-07",
                    "final_trade_decision": "结论：买入\n目标价：22\n止损价：19.68",
                },
            })

        assert r.status_code == 200
        assert mock_send.await_count == 1
        assert mock_send.await_args.args[1] == self.BARK_URL

    def test_report_create_skips_bark_when_disabled(self):
        save_resp = self.client.patch("/v1/config", headers=self.headers, json={
            "bark_url": self.BARK_URL,
            "bark_report_enabled": False,
            "warmup": False,
        })
        assert save_resp.status_code == 200

        with patch("api.services.bark_notification_service.send_report_message_with_retry", new_callable=AsyncMock) as mock_send:
            r = self.client.post("/v1/reports", headers=self.headers, json={
                "symbol": "601958.SH",
                "trade_date": "2026-05-07",
                "decision": "BUY",
                "result_data": {
                    "symbol": "601958.SH",
                    "trade_date": "2026-05-07",
                    "final_trade_decision": "结论：买入\n目标价：22\n止损价：19.68",
                },
            })

        assert r.status_code == 200
        mock_send.assert_not_awaited()


class TestWatchlistAddEndpoint:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = _get_client()
        self.token = _auth_unique(self.client)
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def test_batch_add_supports_codes_and_full_names(self):
        name_to_code = {
            "贵州茅台": "600519.SH",
            "宁德时代": "300750.SZ",
        }
        code_to_name = {value: key for key, value in name_to_code.items()}
        with patch("api.main._load_cn_stock_map", return_value=name_to_code), \
             patch("api.main._get_reverse_stock_map", return_value=code_to_name):
            r = self.client.post("/v1/watchlist", headers=self.headers, json={
                "text": "600519 宁德时代, 未知标的",
            })
        assert r.status_code == 200
        body = r.json()
        assert body["summary"] == {"total": 3, "added": 2, "duplicate": 0, "failed": 1}
        assert [item["status"] for item in body["results"]] == ["added", "added", "invalid"]
        assert body["results"][0]["symbol"] == "600519.SH"
        assert body["results"][1]["symbol"] == "300750.SZ"

    def test_batch_add_marks_duplicates(self):
        name_to_code = {
            "贵州茅台": "600519.SH",
        }
        code_to_name = {value: key for key, value in name_to_code.items()}
        with patch("api.main._load_cn_stock_map", return_value=name_to_code), \
             patch("api.main._get_reverse_stock_map", return_value=code_to_name):
            r = self.client.post("/v1/watchlist", headers=self.headers, json={
                "text": "600519.SH 贵州茅台",
            })
        assert r.status_code == 200
        body = r.json()
        assert body["summary"] == {"total": 2, "added": 1, "duplicate": 1, "failed": 0}
        assert [item["status"] for item in body["results"]] == ["added", "duplicate"]


class TestReportsEndpoint:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = _get_client()
        self.token = _auth_unique(self.client)
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def _create_report(self, symbol: str, trade_date: str, decision: str, result_data: dict | None = None):
        response = self.client.post("/v1/reports", headers=self.headers, json={
            "symbol": symbol,
            "trade_date": trade_date,
            "decision": decision,
            **({"result_data": result_data} if result_data is not None else {}),
        })
        assert response.status_code == 200
        return response.json()

    def test_latest_by_symbols_returns_only_each_symbol_latest_report(self):
        self._create_report("600519.SH", "2026-03-28", "HOLD")
        self._create_report("600519.SH", "2026-03-30", "BUY", {
            "action_label": "条件入场",
            "research_direction": "看多",
            "execution_action": "ENTER",
        })
        self._create_report("300750.SZ", "2026-03-29", "SELL")

        response = self.client.post(
            "/v1/reports/latest-by-symbols",
            headers=self.headers,
            json={"symbols": ["300750.SZ", "600519.SH", "000001.SZ"]},
        )

        assert response.status_code == 200
        body = response.json()
        assert [item["symbol"] for item in body["reports"]] == ["300750.SZ", "600519.SH"]
        assert body["reports"][0]["decision"] == "SELL"
        assert body["reports"][1]["decision"] == "BUY"
        assert body["reports"][1]["action_label"] == "条件入场"
        assert "result_data" not in body["reports"][1]

    def test_batch_delete_endpoint_removes_multiple_reports(self):
        first = self._create_report("600519.SH", "2026-03-28", "HOLD")
        second = self._create_report("300750.SZ", "2026-03-29", "SELL")
        third = self._create_report("000001.SZ", "2026-03-30", "BUY")

        response = self.client.post(
            "/v1/reports/batch/delete",
            headers=self.headers,
            json={"report_ids": [first["id"], second["id"], "missing-report-id"]},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["deleted_ids"] == [first["id"], second["id"]]
        assert body["missing_ids"] == ["missing-report-id"]

        remaining = self.client.get("/v1/reports", headers=self.headers)
        assert remaining.status_code == 200
        remaining_ids = [item["id"] for item in remaining.json()["reports"]]
        assert remaining_ids == [third["id"]]


class TestPortfolioOverviewEndpoint:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = _get_client()
        self.token = _auth_unique(self.client)
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self.name_to_code = {
            "贵州茅台": "600519.SH",
            "宁德时代": "300750.SZ",
        }
        self.code_to_name = {value: key for key, value in self.name_to_code.items()}

    def _add_watchlist(self, text: str):
        with patch("api.main._load_cn_stock_map", return_value=self.name_to_code), \
             patch("api.main._get_reverse_stock_map", return_value=self.code_to_name):
            response = self.client.post("/v1/watchlist", headers=self.headers, json={"text": text})
        assert response.status_code == 200

    def _create_scheduled(self, symbol: str):
        with patch("api.main._get_reverse_stock_map", return_value=self.code_to_name):
            response = self.client.post(
                "/v1/scheduled",
                headers=self.headers,
                json={"symbol": symbol, "horizon": "short", "trigger_time": "20:00"},
            )
        assert response.status_code == 201

    def test_overview_returns_watchlist_scheduled_portfolio_and_latest_reports(self):
        from api.database import ImportedPortfolioPositionDB, get_db_ctx

        self._add_watchlist("600519.SH 300750.SZ")
        self._create_scheduled("600519.SH")

        self.client.post("/v1/reports", headers=self.headers, json={
            "symbol": "600519.SH",
            "trade_date": "2026-03-30",
            "decision": "BUY",
        })
        self.client.post("/v1/reports", headers=self.headers, json={
            "symbol": "300750.SZ",
            "trade_date": "2026-03-29",
            "decision": "SELL",
        })

        current_user = self.client.get("/v1/auth/me", headers=self.headers).json()
        with get_db_ctx() as db:
            db.add(
                ImportedPortfolioPositionDB(
                    id=uuid4().hex,
                    user_id=current_user["id"],
                    source="manual",
                    symbol="600519.SH",
                    security_name="贵州茅台",
                    current_position=300.0,
                    average_cost=1680.5,
                    market_value=504150.0,
                )
            )
            db.commit()

        with patch("api.main._get_reverse_stock_map", return_value=self.code_to_name), \
             patch("api.main._get_reverse_stock_map_cached_only", return_value=self.code_to_name):
            response = self.client.get("/v1/portfolio/overview", headers=self.headers)

        assert response.status_code == 200
        body = response.json()
        assert [item["symbol"] for item in body["watchlist"]] == ["600519.SH", "300750.SZ"]
        assert body["watchlist"][0]["name"] == "贵州茅台"
        assert len(body["scheduled"]) == 1
        assert body["scheduled"][0]["symbol"] == "600519.SH"
        assert body["scheduled"][0]["has_imported_context"] is True
        assert [item["symbol"] for item in body["latest_reports"]] == ["600519.SH", "300750.SZ"]
        assert body["portfolio_import"]["summary"]["positions"] == 1


class TestScheduledBatchEndpoints:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = _get_client()
        self.token = _auth_unique(self.client)
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self.code_to_name = {
            "300750.SZ": "宁德时代",
            "600519.SH": "贵州茅台",
        }

    def _create_scheduled(self, symbol: str):
        with patch("api.main._get_reverse_stock_map", return_value=self.code_to_name):
            response = self.client.post(
                "/v1/scheduled",
                headers=self.headers,
                json={"symbol": symbol, "horizon": "short", "trigger_time": "20:00"},
            )
        assert response.status_code == 201
        return response.json()

    def test_batch_update_endpoint_updates_multiple_items(self):
        first = self._create_scheduled("300750.SZ")
        second = self._create_scheduled("600519.SH")

        with patch("api.main._get_reverse_stock_map", return_value=self.code_to_name):
            response = self.client.patch(
                "/v1/scheduled/batch",
                headers=self.headers,
                json={
                    "item_ids": [first["id"], second["id"]],
                    "horizon": "medium",
                    "trigger_time": "21:30",
                    "is_active": True,
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert [item["horizon"] for item in body["items"]] == ["medium", "medium"]
        assert [item["trigger_time"] for item in body["items"]] == ["21:30", "21:30"]
        assert [item["name"] for item in body["items"]] == ["宁德时代", "贵州茅台"]

    def test_batch_delete_endpoint_removes_multiple_items(self):
        first = self._create_scheduled("300750.SZ")
        second = self._create_scheduled("600519.SH")

        response = self.client.post(
            "/v1/scheduled/batch/delete",
            headers=self.headers,
            json={"item_ids": [first["id"], second["id"]]},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["deleted_ids"] == [first["id"], second["id"]]
        assert body["missing_ids"] == []

        remaining = self.client.get("/v1/scheduled", headers=self.headers)
        assert remaining.status_code == 200
        assert remaining.json()["items"] == []

    def test_manual_trigger_endpoint_queues_single_scheduled_task(self):
        item = self._create_scheduled("300750.SZ")
        run_once = AsyncMock()

        def _close_coro(coro):
            coro.close()
            return MagicMock()

        with patch("api.main._run_manual_trigger", run_once), \
             patch("api.main._create_tracked_task", side_effect=_close_coro), \
             patch("api.main.cn_today_str", return_value="2026-03-31"), \
             patch("api.main._resolve_scheduled_trade_date", return_value="2026-03-31"):
            response = self.client.post(
                f"/v1/scheduled/{item['id']}/trigger",
                headers=self.headers,
            )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "pending"
        assert run_once.call_count == 1

        args, kwargs = run_once.call_args
        assert args[0]["id"] == item["id"]
        assert args[0]["symbol"] == "300750.SZ"
        assert args[0]["user_id"]
        assert args[1] == "2026-03-31"
        assert args[2] == body["job_id"]
        assert kwargs == {}

    def test_batch_trigger_endpoint_queues_selected_tasks_with_position_context(self):
        from api.database import ImportedPortfolioPositionDB, get_db_ctx

        first = self._create_scheduled("300750.SZ")
        second = self._create_scheduled("600519.SH")
        current_user = self.client.get("/v1/auth/me", headers=self.headers).json()

        with get_db_ctx() as db:
            db.query(ImportedPortfolioPositionDB).filter(
                ImportedPortfolioPositionDB.user_id == current_user["id"],
                ImportedPortfolioPositionDB.source == "manual",
                ImportedPortfolioPositionDB.symbol == "600519.SH",
            ).delete()
            db.add(
                ImportedPortfolioPositionDB(
                    id=uuid4().hex,
                    user_id=current_user["id"],
                    source="manual",
                    symbol="600519.SH",
                    security_name="贵州茅台",
                    current_position=300.0,
                    average_cost=1680.5,
                    market_value=504150.0,
                )
            )
            db.commit()

        run_once = AsyncMock()

        def _close_coro(coro):
            coro.close()
            return MagicMock()

        with patch("api.main._run_manual_trigger", run_once), \
             patch("api.main._create_tracked_task", side_effect=_close_coro), \
             patch("api.main.cn_today_str", return_value="2026-03-31"), \
             patch("api.main._resolve_scheduled_trade_date", return_value="2026-03-31"), \
             patch("api.main._get_reverse_stock_map", return_value=self.code_to_name):
            response = self.client.post(
                "/v1/scheduled/batch/trigger",
                headers=self.headers,
                json={"item_ids": [first["id"], second["id"]]},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["summary"] == {
            "total": 2,
            "with_position_context": 1,
        }
        assert [job["symbol"] for job in body["jobs"]] == ["300750.SZ", "600519.SH"]
        assert body["jobs"][0]["current_position"] is None
        assert body["jobs"][0]["average_cost"] is None
        assert body["jobs"][1]["current_position"] == pytest.approx(300.0)
        assert body["jobs"][1]["average_cost"] == pytest.approx(1680.5)
        assert run_once.call_count == 2

        first_args, first_kwargs = run_once.call_args_list[0]
        second_args, second_kwargs = run_once.call_args_list[1]
        assert first_args[0]["id"] == first["id"]
        assert first_args[0]["symbol"] == "300750.SZ"
        assert second_args[0]["id"] == second["id"]
        assert second_args[0]["symbol"] == "600519.SH"
        assert first_args[1] == "2026-03-31"
        assert second_args[1] == "2026-03-31"
        assert first_kwargs == {}
        assert second_kwargs == {}


class TestRuntimeTierGate:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = _get_client()
        self.token = _auth_unique(self.client)
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def test_dry_run_without_runtime_tier_passes(self):
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "symbol": "600519.SH",
            "trade_date": "2024-01-15",
            "dry_run": True,
        })
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        result = _wait_job(self.client, self.token, job_id)
        assert result["status"] == "completed"
        assert result["decision"] == "DRY_RUN"

    def test_direct_query_can_supply_omitted_symbol(self):
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "query": "分析600519.SH的中线建仓机会",
            "trade_date": "2024-01-15",
            "dry_run": True,
        })
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        result = _wait_job(self.client, self.token, job_id)
        assert result["status"] == "completed"
        assert result["result"]["symbol"] == "600519.SH"

    def test_explicit_ss_alias_is_canonicalized(self):
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "symbol": "600519.SS",
            "trade_date": "2024-01-15",
            "dry_run": True,
        })

        assert r.status_code == 200
        result = _wait_job(self.client, self.token, r.json()["job_id"])
        assert result["status"] == "completed"
        assert result["result"]["symbol"] == "600519.SH"

    def test_company_name_query_is_resolved_before_job_creation(self, monkeypatch):
        import api.main as main_module

        monkeypatch.setattr(
            main_module,
            "_load_cn_stock_map",
            lambda: {"贵州茅台": "600519.SH"},
        )
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "query": "分析贵州茅台的中线机会",
            "trade_date": "2024-01-15",
            "dry_run": True,
        })
        assert r.status_code == 200
        result = _wait_job(self.client, self.token, r.json()["job_id"])
        assert result["status"] == "completed"
        assert result["result"]["symbol"] == "600519.SH"

    def test_direct_company_name_symbol_is_resolved_before_validation(self, monkeypatch):
        import api.main as main_module

        monkeypatch.setattr(
            main_module,
            "_load_cn_stock_map",
            lambda: {"贵州茅台": "600519.SH"},
        )
        response = self.client.post(
            "/v1/analyze",
            headers=self.headers,
            json={
                "symbol": "贵州茅台",
                "trade_date": "2024-01-15",
                "dry_run": True,
            },
        )

        assert response.status_code == 200
        result = _wait_job(self.client, self.token, response.json()["job_id"])
        assert result["status"] == "completed"
        assert result["result"]["symbol"] == "600519.SH"

    def test_supplied_symbol_rejects_mismatched_company_name_pair(self, monkeypatch):
        import api.main as main_module

        monkeypatch.setattr(
            main_module,
            "_load_cn_stock_map",
            lambda: {"五粮液": "000858.SZ", "贵州茅台": "600519.SH"},
        )
        monkeypatch.setattr(main_module, "_cn_stock_map", None)

        response = self.client.post(
            "/v1/analyze",
            headers=self.headers,
            json={
                "symbol": "600519.SH",
                "query": "分析五粮液(600519.SH)",
                "trade_date": "2024-01-15",
                "dry_run": True,
            },
        )

        assert response.status_code == 422
        assert "公司名称与代码" in response.json()["detail"]

    @pytest.mark.parametrize(
        "query",
        ("预算100000元", "持仓100000股", "可用资金100000，分析风险"),
    )
    def test_supplied_symbol_accepts_six_digit_amounts(self, query):
        response = self.client.post(
            "/v1/analyze",
            headers=self.headers,
            json={
                "symbol": "600519.SH",
                "query": query,
                "trade_date": "2024-01-15",
                "dry_run": True,
            },
        )

        assert response.status_code == 200
        result = _wait_job(self.client, self.token, response.json()["job_id"])
        assert result["status"] == "completed"
        assert result["result"]["symbol"] == "600519.SH"

    def test_supplied_symbol_generic_query_does_not_load_stock_map(self, monkeypatch):
        import api.main as main_module

        def fail_if_loaded():
            raise AssertionError("generic supplied-symbol query must not load stock map")

        monkeypatch.setattr(main_module, "_load_cn_stock_map", fail_if_loaded)
        monkeypatch.setattr(main_module, "_cn_stock_map", None)
        response = self.client.post(
            "/v1/analyze",
            headers=self.headers,
            json={
                "symbol": "600519.SH",
                "query": "帮我分析一下是否值得买",
                "trade_date": "2024-01-15",
                "dry_run": True,
            },
        )

        assert response.status_code == 200
        result = _wait_job(self.client, self.token, response.json()["job_id"])
        assert result["status"] == "completed"
        assert result["result"]["symbol"] == "600519.SH"

    def test_st_company_name_is_not_ambiguous_with_us_ticker(self, monkeypatch):
        import api.main as main_module

        monkeypatch.setattr(
            main_module,
            "_load_cn_stock_map",
            lambda: {"ST华微": "600360.SH"},
        )
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "query": "分析ST华微的机会",
            "trade_date": "2024-01-15",
            "dry_run": True,
        })

        assert r.status_code == 200
        result = _wait_job(self.client, self.token, r.json()["job_id"])
        assert result["status"] == "completed"
        assert result["result"]["symbol"] == "600360.SH"

    def test_latin_prefixed_company_name_is_not_ambiguous(self, monkeypatch):
        import api.main as main_module

        monkeypatch.setattr(
            main_module,
            "_load_cn_stock_map",
            lambda: {"TCL科技": "000100.SZ"},
        )
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "query": "分析TCL科技",
            "trade_date": "2024-01-15",
            "dry_run": True,
        })

        assert r.status_code == 200
        result = _wait_job(self.client, self.token, r.json()["job_id"])
        assert result["status"] == "completed"
        assert result["result"]["symbol"] == "000100.SZ"

    def test_latin_prefixed_company_name_with_question_is_not_ambiguous(
        self, monkeypatch
    ):
        import api.main as main_module

        monkeypatch.setattr(
            main_module,
            "_load_cn_stock_map",
            lambda: {"TCL科技": "000100.SZ"},
        )
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "query": "TCL科技值得买吗",
            "trade_date": "2024-01-15",
            "dry_run": True,
        })

        assert r.status_code == 200
        result = _wait_job(self.client, self.token, r.json()["job_id"])
        assert result["status"] == "completed"
        assert result["result"]["symbol"] == "000100.SZ"

    def test_unresolved_omitted_symbol_is_rejected_without_job(self, monkeypatch):
        import api.main as main_module

        monkeypatch.setattr(main_module, "_load_cn_stock_map", lambda: {})
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "query": "分析一家未知公司的PE和ROE",
            "trade_date": "2024-01-15",
            "dry_run": True,
        })
        assert r.status_code == 422
        assert "无法唯一识别分析标的" in r.json()["detail"]

    def test_unknown_exchange_suffix_is_rejected_without_job(self):
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "query": "分析600519.XX",
            "trade_date": "2024-01-15",
            "dry_run": True,
        })

        assert r.status_code == 422
        assert "无法唯一识别分析标的" in r.json()["detail"]

    def test_supplied_symbol_rejects_unresolved_query_code_without_job(self):
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "symbol": "AAPL",
            "query": "分析600519.XX",
            "trade_date": "2024-01-15",
            "dry_run": True,
        })

        assert r.status_code == 422
        assert "代码无法确认" in r.json()["detail"]

    def test_explicit_wordlike_us_ticker_is_accepted(self):
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "query": "Analyze AI",
            "trade_date": "2024-01-15",
            "dry_run": True,
        })

        assert r.status_code == 200
        result = _wait_job(self.client, self.token, r.json()["job_id"])
        assert result["status"] == "completed"
        assert result["result"]["symbol"] == "AI"

    def test_lowercase_us_ticker_in_explicit_context_is_accepted(self):
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "query": "analyze aapl",
            "trade_date": "2024-01-15",
            "dry_run": True,
        })

        assert r.status_code == 200
        result = _wait_job(self.client, self.token, r.json()["job_id"])
        assert result["status"] == "completed"
        assert result["result"]["symbol"] == "AAPL"

    def test_multi_us_ticker_chinese_query_is_rejected(self):
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "query": "比较AAPL和MSFT",
            "trade_date": "2024-01-15",
            "dry_run": True,
        })

        assert r.status_code == 422
        assert "一次分析只支持一个明确标的" in r.json()["detail"]

    def test_light_research_without_confirmation_passes(self):
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "symbol": "600519.SH",
            "trade_date": "2024-01-15",
            "dry_run": True,
            "runtime_tier": "LIGHT_RESEARCH",
        })
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        result = _wait_job(self.client, self.token, job_id)
        assert result["status"] == "completed"

    def test_full_ta_without_confirmation_rejected(self):
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "symbol": "600519.SH",
            "trade_date": "2024-01-15",
            "dry_run": False,
            "runtime_tier": "FULL_TA",
        })
        assert r.status_code == 403
        assert "confirmed_full_ta" in r.json()["detail"]["message"]

    def test_full_ta_with_confirmation_accepted(self):
        r = self.client.post("/v1/analyze", headers=self.headers, json={
            "symbol": "600519.SH",
            "trade_date": "2024-01-15",
            "dry_run": True,
            "runtime_tier": "FULL_TA",
            "confirmed_full_ta": True,
        })
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        result = _wait_job(self.client, self.token, job_id)
        assert result["status"] == "completed"
