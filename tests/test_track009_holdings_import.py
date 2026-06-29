"""TRACK-009 — Holdings import dry-run / validate / contract tests.

Covers the parse_positions_text → validate_positions → dry_run_import →
import_positions_from_text pipeline plus the OpenClaw contract endpoint.

Key invariants enforced (per AGENTS.md adversarial-review mindset):
  - dry_run_import MUST NOT call db.commit / db.add / db.delete.
  - import_positions_from_text MUST route through sync_positions
    (no side-channel writes).
  - Repeat import MUST NOT clear user data unless the incoming snapshot
    explicitly omits the symbol (snapshot-replace semantics).
  - ImportedPortfolioPositionDB has no `notes` column, so the
    "重复导入不清空 notes" guarantee is structural; we still assert the
    write path doesn't lose data on idempotent re-import.
  - Strong-action verbs in any free-text field get scrubbed.
  - All-invalid input MUST raise (never commit an empty snapshot that
    would silently erase a user's holdings).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base, ImportedPortfolioPositionDB, UserDB
from api.runtime_tier import RuntimeTier, tradeflow_endpoint_tier
from api.services import portfolio_import_service


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
    email = auth_service.normalize_email(f"track009-{uuid4().hex[:8]}@test.com")
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


# ---------------------------------------------------------------------------
# parse_positions_text
# ---------------------------------------------------------------------------

class TestParseJson:
    def test_parses_json_array(self):
        text = json.dumps([
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100, "average_cost": 1700},
            {"symbol": "000001", "name": "平安银行", "shares": 200, "cost": 12.5},
        ])
        rows = portfolio_import_service.parse_positions_text(text)
        assert len(rows) == 2
        assert rows[0]["symbol"] == "600519.SH"
        assert rows[0]["name"] == "贵州茅台"
        # alias mapping: shares → current_position, cost → average_cost
        assert rows[1]["current_position"] in ("200", 200)
        assert rows[1]["average_cost"] in ("12.5", 12.5)

    def test_parses_positions_envelope(self):
        text = json.dumps({"positions": [
            {"symbol": "600519.SH", "name": "贵州茅台"},
        ]})
        rows = portfolio_import_service.parse_positions_text(text)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "600519.SH"

    def test_parses_single_object(self):
        text = json.dumps({"symbol": "600519.SH", "name": "贵州茅台"})
        rows = portfolio_import_service.parse_positions_text(text)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "600519.SH"

    def test_invalid_json_raises_value_error(self):
        with pytest.raises(ValueError, match="JSON 解析失败"):
            portfolio_import_service.parse_positions_text("[not valid json")

    def test_empty_input_returns_empty_list(self):
        assert portfolio_import_service.parse_positions_text("") == []
        assert portfolio_import_service.parse_positions_text("   \n  ") == []

    def test_bom_is_stripped(self):
        text = "\ufeff" + json.dumps([{"symbol": "600519.SH"}])
        rows = portfolio_import_service.parse_positions_text(text)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "600519.SH"


class TestParseCsv:
    def test_parses_chinese_header_csv(self):
        text = "代码,名称,持仓数,成本价,市值\n600519.SH,贵州茅台,100,1700,170000\n300750.SZ,宁德时代,200,205.5,41100"
        rows = portfolio_import_service.parse_positions_text(text)
        assert len(rows) == 2
        assert rows[0]["symbol"] == "600519.SH"
        assert rows[0]["name"] == "贵州茅台"
        assert rows[0]["current_position"] == "100"
        assert rows[0]["average_cost"] == "1700"
        assert rows[0]["market_value"] == "170000"

    def test_parses_english_header_csv(self):
        text = "symbol,name,shares,cost,value\n600519.SH,Moutai,100,1700,170000"
        rows = portfolio_import_service.parse_positions_text(text)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "600519.SH"
        assert rows[0]["current_position"] == "100"

    def test_parses_tsv(self):
        text = "代码\t名称\t持仓数\n600519.SH\t贵州茅台\t100"
        rows = portfolio_import_service.parse_positions_text(text)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "600519.SH"
        assert rows[0]["current_position"] == "100"

    def test_comment_lines_skipped(self):
        text = "# my holdings\nsymbol,shares\n600519.SH,100\n# end"
        rows = portfolio_import_service.parse_positions_text(text)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "600519.SH"


class TestParseWhitespace:
    def test_code_name_shares_cost_value(self):
        rows = portfolio_import_service.parse_positions_text("600519 贵州茅台 100 1700 170000")
        assert len(rows) == 1
        assert rows[0]["symbol"] == "600519"
        assert rows[0]["name"] == "贵州茅台"
        assert rows[0]["current_position"] == "100"
        assert rows[0]["average_cost"] == "1700"
        assert rows[0]["market_value"] == "170000"

    def test_code_only(self):
        rows = portfolio_import_service.parse_positions_text("600519")
        assert len(rows) == 1
        assert rows[0]["symbol"] == "600519"
        assert "name" not in rows[0] or rows[0].get("name") is None

    def test_code_and_shares_no_name(self):
        rows = portfolio_import_service.parse_positions_text("600519 100 1700")
        assert len(rows) == 1
        assert rows[0]["symbol"] == "600519"
        assert rows[0]["current_position"] == "100"
        assert rows[0]["average_cost"] == "1700"


# ---------------------------------------------------------------------------
# validate_positions
# ---------------------------------------------------------------------------

class TestValidatePositions:
    def test_valid_passes(self):
        result = portfolio_import_service.validate_positions([
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100, "average_cost": 1700},
        ])
        assert result["valid_count"] == 1
        assert result["invalid_count"] == 0
        assert result["valid"][0]["symbol"] == "600519.SH"

    def test_normalizes_bare_code(self):
        result = portfolio_import_service.validate_positions([
            {"symbol": "600519", "current_position": 100},
        ])
        assert result["valid_count"] == 1
        assert result["valid"][0]["symbol"] == "600519.SH"

    def test_invalid_symbol(self):
        result = portfolio_import_service.validate_positions([
            {"symbol": "ABC", "current_position": 100},
            {"symbol": "", "current_position": 100},
            {"symbol": "12345", "current_position": 100},  # 5 digits → invalid
        ])
        assert result["valid_count"] == 0
        assert result["invalid_count"] == 3
        reasons = [e["reason"] for e in result["invalid"]]
        assert all(r == "invalid_symbol" for r in reasons)

    def test_negative_shares_invalid(self):
        result = portfolio_import_service.validate_positions([
            {"symbol": "600519.SH", "current_position": -100},
        ])
        assert result["valid_count"] == 0
        assert result["invalid_count"] == 1
        assert result["invalid"][0]["reason"] == "negative_field"
        assert "current_position" in result["invalid"][0]["fields"]

    def test_negative_cost_invalid(self):
        result = portfolio_import_service.validate_positions([
            {"symbol": "600519.SH", "average_cost": -50.5},
        ])
        assert result["invalid_count"] == 1
        assert "average_cost" in result["invalid"][0]["fields"]

    def test_zero_shares_allowed(self):
        result = portfolio_import_service.validate_positions([
            {"symbol": "600519.SH", "current_position": 0},
        ])
        assert result["valid_count"] == 1

    def test_duplicate_symbol_within_blob(self):
        result = portfolio_import_service.validate_positions([
            {"symbol": "600519.SH", "current_position": 100},
            {"symbol": "600519", "current_position": 200},  # normalizes to same
        ])
        assert result["valid_count"] == 1
        assert result["valid"][0]["current_position"] == 100
        assert result["invalid_count"] == 1
        assert result["invalid"][0]["reason"] == "duplicate_symbol"

    def test_missing_name_warns(self):
        result = portfolio_import_service.validate_positions([
            {"symbol": "600519.SH", "current_position": 100},
        ])
        reasons = [w["reason"] for w in result["warnings"]]
        assert "missing_name" in reasons

    def test_unparseable_number_warns(self):
        result = portfolio_import_service.validate_positions([
            {"symbol": "600519.SH", "current_position": "N/A"},
        ])
        assert result["valid_count"] == 1
        reasons = [w["reason"] for w in result["warnings"]]
        assert "unparseable_current_position" in reasons

    def test_scrubs_strong_action_verbs_in_name(self):
        result = portfolio_import_service.validate_positions([
            {"symbol": "600519.SH", "name": "贵州茅台 立即买入"},
        ])
        assert result["valid_count"] == 1
        assert "立即买入" not in (result["valid"][0]["name"] or "")

    def test_non_list_raises(self):
        with pytest.raises(ValueError, match="positions 必须为列表"):
            portfolio_import_service.validate_positions("not a list")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# dry_run_import — NO DB writes
# ---------------------------------------------------------------------------

class TestDryRunImport:
    def test_dry_run_does_not_write_to_db(self, db):
        before = db.query(ImportedPortfolioPositionDB).count()
        result = portfolio_import_service.dry_run_import(
            db=db,
            user_id="user1",
            positions=[
                {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100, "average_cost": 1700},
            ],
            source="openclaw",
        )
        after = db.query(ImportedPortfolioPositionDB).count()
        assert after == before, "dry_run_import must not persist any rows"
        assert result["dry_run"] is True
        assert result["added_count"] == 1
        assert result["updated_count"] == 0

    def test_added_when_symbol_new(self, db):
        result = portfolio_import_service.dry_run_import(
            db=db,
            user_id="user1",
            positions=[{"symbol": "600519.SH", "current_position": 100}],
            source="openclaw",
        )
        assert result["added_count"] == 1
        assert result["updated_count"] == 0
        assert result["removed_count"] == 0
        assert result["unchanged_count"] == 0

    def test_unchanged_when_identical(self, db):
        portfolio_import_service.sync_positions(
            db=db, user_id="user1",
            positions=[{"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100, "average_cost": 1700}],
            source="openclaw",
        )
        result = portfolio_import_service.dry_run_import(
            db=db, user_id="user1",
            positions=[{"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100, "average_cost": 1700}],
            source="openclaw",
        )
        assert result["unchanged_count"] == 1
        assert result["added_count"] == 0
        assert result["updated_count"] == 0
        assert result["removed_count"] == 0

    def test_updated_when_field_changes(self, db):
        portfolio_import_service.sync_positions(
            db=db, user_id="user1",
            positions=[{"symbol": "600519.SH", "current_position": 100, "average_cost": 1700}],
            source="openclaw",
        )
        result = portfolio_import_service.dry_run_import(
            db=db, user_id="user1",
            positions=[{"symbol": "600519.SH", "current_position": 200, "average_cost": 1700}],
            source="openclaw",
        )
        assert result["updated_count"] == 1
        assert result["unchanged_count"] == 0
        delta = result["updated"][0]["delta"]
        assert "current_position" in delta
        assert delta["current_position"]["before"] == 100
        assert delta["current_position"]["after"] == 200

    def test_removed_when_symbol_dropped(self, db):
        portfolio_import_service.sync_positions(
            db=db, user_id="user1",
            positions=[
                {"symbol": "600519.SH", "current_position": 100},
                {"symbol": "300750.SZ", "current_position": 200},
            ],
            source="openclaw",
        )
        # New snapshot drops 600519.SH
        result = portfolio_import_service.dry_run_import(
            db=db, user_id="user1",
            positions=[{"symbol": "300750.SZ", "current_position": 200}],
            source="openclaw",
        )
        assert result["removed_count"] == 1
        assert result["removed"][0]["symbol"] == "600519.SH"

    def test_invalid_rows_go_to_errors(self, db):
        result = portfolio_import_service.dry_run_import(
            db=db, user_id="user1",
            positions=[
                {"symbol": "600519.SH", "current_position": 100},
                {"symbol": "INVALID", "current_position": 50},
                {"symbol": "600519", "current_position": -10},  # dup after norm + negative
            ],
            source="openclaw",
        )
        assert result["added_count"] == 1
        assert result["error_count"] >= 1

    def test_source_isolation(self, db):
        portfolio_import_service.sync_positions(
            db=db, user_id="user1",
            positions=[{"symbol": "600519.SH", "current_position": 100}],
            source="manual",
        )
        # Same symbol under different source → still added (not unchanged)
        result = portfolio_import_service.dry_run_import(
            db=db, user_id="user1",
            positions=[{"symbol": "600519.SH", "current_position": 100}],
            source="openclaw",
        )
        assert result["added_count"] == 1

    def test_user_isolation(self, db):
        portfolio_import_service.sync_positions(
            db=db, user_id="user-a",
            positions=[{"symbol": "600519.SH", "current_position": 100}],
            source="openclaw",
        )
        result = portfolio_import_service.dry_run_import(
            db=db, user_id="user-b",
            positions=[{"symbol": "600519.SH", "current_position": 100}],
            source="openclaw",
        )
        assert result["added_count"] == 1  # different user → brand new

    def test_write_semantics_marker(self, db):
        result = portfolio_import_service.dry_run_import(
            db=db, user_id="user1",
            positions=[{"symbol": "600519.SH"}],
            source="openclaw",
        )
        assert result["write_semantics"] == "replace_snapshot"


# ---------------------------------------------------------------------------
# import_positions_from_text — commit through sync_positions
# ---------------------------------------------------------------------------

class TestImportFromText:
    def test_imports_json_then_state_visible(self, db):
        text = json.dumps([
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100, "average_cost": 1700},
        ])
        result = portfolio_import_service.import_positions_from_text(
            db=db, user_id="user1", text=text, source="openclaw",
        )
        assert result["dry_run"]["added_count"] == 1
        assert result["state"]["summary"]["positions"] == 1
        symbols = [p["symbol"] for p in result["state"]["positions"]]
        assert "600519.SH" in symbols

    def test_imports_csv(self, db):
        text = "代码,名称,持仓数,成本价\n600519.SH,贵州茅台,100,1700"
        result = portfolio_import_service.import_positions_from_text(
            db=db, user_id="user1", text=text, source="openclaw",
        )
        assert result["state"]["summary"]["positions"] == 1

    def test_imports_whitespace(self, db):
        text = "600519 贵州茅台 100 1700 170000"
        result = portfolio_import_service.import_positions_from_text(
            db=db, user_id="user1", text=text, source="openclaw",
        )
        assert result["state"]["summary"]["positions"] == 1

    def test_empty_text_raises(self, db):
        with pytest.raises(ValueError, match="导入文本为空"):
            portfolio_import_service.import_positions_from_text(
                db=db, user_id="user1", text="", source="openclaw",
            )

    def test_all_invalid_raises_instead_of_erasing(self, db):
        # Seed existing holdings
        portfolio_import_service.sync_positions(
            db=db, user_id="user1",
            positions=[{"symbol": "600519.SH", "current_position": 100}],
            source="openclaw",
        )
        # Now try to import garbage
        with pytest.raises(ValueError, match="全部无效"):
            portfolio_import_service.import_positions_from_text(
                db=db, user_id="user1",
                text="INVALID\nALSO_INVALID",
                source="openclaw",
            )
        # Existing data MUST still be there.
        existing = portfolio_import_service.list_imported_positions(db, "user1")
        assert any(p["symbol"] == "600519.SH" for p in existing)

    def test_repeat_import_preserves_data(self, db):
        """TRACK-009 acceptance: 重复导入不清空 notes.

        ImportedPortfolioPositionDB has no notes column, but we still verify
        that re-importing the same snapshot is idempotent and doesn't lose
        any tracked field.
        """
        text = json.dumps([
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100, "average_cost": 1700},
        ])
        portfolio_import_service.import_positions_from_text(
            db=db, user_id="user1", text=text, source="openclaw",
        )
        # Import the exact same thing again
        portfolio_import_service.import_positions_from_text(
            db=db, user_id="user1", text=text, source="openclaw",
        )
        positions = portfolio_import_service.list_imported_positions(db, "user1")
        assert len(positions) == 1
        p = positions[0]
        assert p["symbol"] == "600519.SH"
        assert p["name"] == "贵州茅台"
        assert p["current_position"] == 100
        assert p["average_cost"] == 1700

    def test_repeat_import_with_update_documents_snapshot_replace(self, db):
        """Document snapshot-replace semantics: re-import without a field
        and that field becomes None. The dry-run preview surfaces this so
        users aren't surprised. This is the tradeoff of using the canonical
        sync_positions write path (required by TRACK-009)."""
        text1 = json.dumps([
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100, "average_cost": 1700, "market_value": 170000},
        ])
        portfolio_import_service.import_positions_from_text(
            db=db, user_id="user1", text=text1, source="openclaw",
        )
        # Re-import updating only shares; since sync_positions replaces the
        # snapshot, market_value will be lost unless re-supplied.
        text2 = json.dumps([
            {"symbol": "600519.SH", "current_position": 200},
        ])
        result = portfolio_import_service.import_positions_from_text(
            db=db, user_id="user1", text=text2, source="openclaw",
        )
        # Diff preview must show this clearly.
        assert result["dry_run"]["updated_count"] == 1
        delta = result["dry_run"]["updated"][0]["delta"]
        assert "current_position" in delta
        # market_value WILL be lost → it's part of the diff.
        assert "market_value" in delta
        p = portfolio_import_service.list_imported_positions(db, "user1")[0]
        assert p["current_position"] == 200
        assert p["market_value"] is None


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

class TestContract:
    def test_contract_has_required_fields(self):
        c = portfolio_import_service.OPENCLAW_HOLDINGS_CONTRACT
        assert c["schema_version"]
        assert c["contract_for"] == "openclaw_to_ta_holdings"
        assert len(c["write_endpoints"]) >= 3
        assert len(c["read_endpoints"]) >= 1
        assert "symbol" in c["field_semantics"]
        assert "source" in c["field_semantics"]
        assert any("dry-run" in ep["path"] for ep in c["write_endpoints"])

    def test_contract_does_not_contain_strong_action_verbs(self):
        from api.services.investment_controller_context import _STRONG_ACTION_PATTERNS
        c = portfolio_import_service.OPENCLAW_HOLDINGS_CONTRACT
        blob = json.dumps(c, ensure_ascii=False)
        for verb in _STRONG_ACTION_PATTERNS:
            assert verb not in blob, f"contract leaked strong action verb: {verb}"


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------

class TestHoldingsImportApi:
    def test_contract_endpoint_returns_stable_structure(self):
        from api.main import app
        client = TestClient(app, raise_server_exceptions=False)
        token = _auth_unique(client)
        resp = client.get(
            "/v1/portfolio/imports/contract",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["schema_version"]
        assert any(ep["path"] == "/v1/portfolio/imports/dry-run" for ep in body["write_endpoints"])

    def test_dry_run_endpoint_with_text(self):
        from api.main import app
        client = TestClient(app, raise_server_exceptions=False)
        token = _auth_unique(client)
        resp = client.post(
            "/v1/portfolio/imports/dry-run",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "text": json.dumps([
                    {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100, "average_cost": 1700},
                ]),
                "source": "openclaw",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["dry_run"] is True
        assert body["added_count"] == 1
        assert body["source"] == "openclaw"

    def test_dry_run_endpoint_with_positions(self):
        from api.main import app
        client = TestClient(app, raise_server_exceptions=False)
        token = _auth_unique(client)
        resp = client.post(
            "/v1/portfolio/imports/dry-run",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "positions": [
                    {"symbol": "600519.SH", "current_position": 100},
                ],
                "source": "openclaw",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["added_count"] == 1

    def test_dry_run_endpoint_rejects_empty_payload(self):
        from api.main import app
        client = TestClient(app, raise_server_exceptions=False)
        token = _auth_unique(client)
        resp = client.post(
            "/v1/portfolio/imports/dry-run",
            headers={"Authorization": f"Bearer {token}"},
            json={"source": "openclaw"},
        )
        assert resp.status_code == 400

    def test_dry_run_endpoint_does_not_persist(self):
        from api.main import app
        client = TestClient(app, raise_server_exceptions=False)
        token = _auth_unique(client)
        headers = {"Authorization": f"Bearer {token}"}
        client.post(
            "/v1/portfolio/imports/dry-run",
            headers=headers,
            json={
                "text": json.dumps([{"symbol": "600519.SH", "current_position": 100}]),
                "source": "openclaw",
            },
        )
        state = client.get("/v1/portfolio/imports", headers=headers).json()
        assert state["summary"]["positions"] == 0, "dry-run endpoint must not write"

    def test_import_text_endpoint_commits(self):
        from api.main import app
        client = TestClient(app, raise_server_exceptions=False)
        token = _auth_unique(client)
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.post(
            "/v1/portfolio/imports/import-text",
            headers=headers,
            json={
                "text": json.dumps([
                    {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100, "average_cost": 1700},
                ]),
                "source": "openclaw",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["dry_run"]["added_count"] == 1
        assert body["state"]["summary"]["positions"] == 1
        # Verify via the canonical read endpoint.
        state = client.get("/v1/portfolio/imports", headers=headers).json()
        assert any(p["symbol"] == "600519.SH" for p in state["positions"])

    def test_import_text_endpoint_rejects_invalid_json(self):
        from api.main import app
        client = TestClient(app, raise_server_exceptions=False)
        token = _auth_unique(client)
        resp = client.post(
            "/v1/portfolio/imports/import-text",
            headers={"Authorization": f"Bearer {token}"},
            json={"text": "[not json", "source": "openclaw"},
        )
        assert resp.status_code == 400

    def test_import_text_endpoint_rejects_all_invalid(self):
        from api.main import app
        client = TestClient(app, raise_server_exceptions=False)
        token = _auth_unique(client)
        headers = {"Authorization": f"Bearer {token}"}
        # Seed an existing holding
        client.post(
            "/v1/portfolio/imports",
            headers=headers,
            json={
                "positions": [{"symbol": "600519.SH", "current_position": 100}],
                "source": "openclaw",
            },
        )
        # Try to import garbage
        resp = client.post(
            "/v1/portfolio/imports/import-text",
            headers=headers,
            json={"text": "INVALID_CODE", "source": "openclaw"},
        )
        assert resp.status_code == 400
        # Existing data still there
        state = client.get("/v1/portfolio/imports", headers=headers).json()
        assert any(p["symbol"] == "600519.SH" for p in state["positions"])

    def test_dry_run_then_import_roundtrip(self):
        """End-to-end: dry-run preview → confirm → commit → re-dry-run shows unchanged."""
        from api.main import app
        client = TestClient(app, raise_server_exceptions=False)
        token = _auth_unique(client)
        headers = {"Authorization": f"Bearer {token}"}
        text = json.dumps([
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100, "average_cost": 1700},
        ])
        # 1. dry-run
        dr = client.post("/v1/portfolio/imports/dry-run", headers=headers,
                         json={"text": text, "source": "openclaw"}).json()
        assert dr["added_count"] == 1
        # 2. commit
        imp = client.post("/v1/portfolio/imports/import-text", headers=headers,
                          json={"text": text, "source": "openclaw"}).json()
        assert imp["dry_run"]["added_count"] == 1
        # 3. re-dry-run → unchanged
        dr2 = client.post("/v1/portfolio/imports/dry-run", headers=headers,
                          json={"text": text, "source": "openclaw"}).json()
        assert dr2["unchanged_count"] == 1
        assert dr2["added_count"] == 0


# ---------------------------------------------------------------------------
# Runtime tier
# ---------------------------------------------------------------------------

class TestRuntimeTier:
    def test_dry_run_endpoint_registered_as_fast(self):
        assert (
            tradeflow_endpoint_tier("portfolio_holdings_import_dry_run")
            == RuntimeTier.FAST_RADAR
        )

    def test_text_import_endpoint_registered_as_fast(self):
        assert (
            tradeflow_endpoint_tier("portfolio_holdings_import_text")
            == RuntimeTier.FAST_RADAR
        )

    def test_contract_endpoint_registered_as_fast(self):
        assert (
            tradeflow_endpoint_tier("portfolio_holdings_import_contract")
            == RuntimeTier.FAST_RADAR
        )
