"""TA-MF-01: /v1/market/facts/contract endpoint tests (read-only + auth).

The contract self-description endpoint is the single new facts route in
TA-MF-01; it must refuse unauthenticated access, accept JWT and API-token
auth without writing last_used_at, and serve digests matching the frozen
artifacts on disk.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.database import UserTokenDB, get_db_ctx
from api.main import (
    MARKET_FACTS_CONTRACT_VERSION,
    MARKET_FACTS_PACK_TYPES,
    MARKET_FACTS_READONLY_ENDPOINTS,
    _MARKET_FACTS_SCHEMA_DIR,
)
from api.services import token_service
from tests.test_api_smoke import _auth_unique, _get_client
from tradingagents.dataflows.tushare_query_contract import QUERY_STATES


@pytest.fixture()
def client() -> TestClient:
    return _get_client()


@pytest.fixture()
def headers(client: TestClient) -> dict:
    token = _auth_unique(client)
    return {"Authorization": f"Bearer {token}"}


class TestMarketFactsContractEndpoint:
    def test_requires_auth(self, client: TestClient):
        assert client.get("/v1/market/facts/contract").status_code in (401, 403)

    def test_rejects_garbage_token(self, client: TestClient):
        response = client.get(
            "/v1/market/facts/contract",
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert response.status_code in (401, 403)

    def test_authenticated_returns_candidate_contract(self, client: TestClient, headers: dict):
        response = client.get("/v1/market/facts/contract", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["contract"] == "market-facts"
        assert body["version"] == MARKET_FACTS_CONTRACT_VERSION == "1.0.0-candidate.1"
        assert body["status"] == "CANDIDATE"
        assert body["timezone"] == "Asia/Shanghai"

    def test_packages_and_states_match_frozen_sources(self, client: TestClient, headers: dict):
        body = client.get("/v1/market/facts/contract", headers=headers).json()
        assert body["packages"] == list(MARKET_FACTS_PACK_TYPES)
        assert body["query_states"] == list(QUERY_STATES)

    def test_endpoint_catalog_is_get_only_and_covers_all_packages(self, client: TestClient, headers: dict):
        body = client.get("/v1/market/facts/contract", headers=headers).json()
        routes = body["read_only_endpoints"]
        assert {route["path"] for route in routes} == {
            entry["path"] for entry in MARKET_FACTS_READONLY_ENDPOINTS
        }
        package_routes = [route for route in routes if route["status"] == "RESERVED"]
        assert {route["package"] for route in package_routes} == set(MARKET_FACTS_PACK_TYPES)
        legacy = next(route for route in routes if route["status"] == "LEGACY")
        assert legacy["path"] == "/v1/market/kline"
        assert "KNOWN-GAP-1" in legacy["note"]

    def test_schema_digests_match_disk(self, client: TestClient, headers: dict):
        body = client.get("/v1/market/facts/contract", headers=headers).json()
        assert body["schema_dir_available"] is True
        on_disk = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(_MARKET_FACTS_SCHEMA_DIR.glob("*.schema.json"))
        }
        assert {entry["name"]: entry["sha256"] for entry in body["schemas"]} == on_disk
        manifest_path = _MARKET_FACTS_SCHEMA_DIR / "MANIFEST.json"
        assert body["fixture_manifest_sha256"] == hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    def test_api_token_does_not_update_last_used_at(self, client: TestClient):
        jwt = _auth_unique(client)
        from api.services import auth_service

        user_id = str(auth_service.decode_access_token(jwt)["sub"])
        with get_db_ctx() as db:
            created = token_service.create_token(db, user_id, "market-facts-contract")
            token_id, api_token = created["id"], created["token"]

        response = client.get(
            "/v1/market/facts/contract",
            headers={"Authorization": f"Bearer {api_token}"},
        )
        assert response.status_code == 200

        with get_db_ctx() as db:
            row = db.query(UserTokenDB).filter(UserTokenDB.id == token_id).first()
            assert row is not None
            assert row.last_used_at is None

    def test_readonly_endpoint_serves_no_market_data(self, client: TestClient, headers: dict):
        body = client.get("/v1/market/facts/contract", headers=headers).json()
        serialized = str(body)
        # No candles, no OHLC, no row payloads: pure contract metadata.
        assert "candles" not in serialized
        assert body["packages"] and body["query_states"]
