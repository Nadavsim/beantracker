import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from beantracker.config import Settings
from beantracker.models.orm import Base

pytestmark = pytest.mark.integration

TEST_DB = "beantracker_test"


def test_migrations_match_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Upgrading an empty DB must yield exactly the schema the ORM models describe.

    Uses a dedicated database so dev data is never touched.
    """
    base = make_url(Settings().database_url)
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {TEST_DB}"))
    test_url = base.set(database=TEST_DB).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", test_url)  # alembic env.py reads Settings()

    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(test_url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []

    command.downgrade(Config("alembic.ini"), "base")
    engine.dispose()
