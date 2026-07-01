from pathlib import Path


def test_pytest_database_url_is_not_project_production_db():
    from api import database

    project_db = Path(__file__).resolve().parents[1] / "tradingagents.db"
    db_url = database.DATABASE_URL
    assert db_url.startswith("sqlite")
    db_path = db_url.replace("sqlite:///", "").replace("sqlite://", "")
    assert Path(db_path).resolve() != project_db.resolve()
