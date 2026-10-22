from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from beantracker.config import Settings
from beantracker.db import make_engine, make_session_factory

TEST_DB = "beantracker_test"


@pytest.fixture
def test_db_url(monkeypatch: pytest.MonkeyPatch) -> str:
    """A freshly created, empty database used only by tests (dev data is never touched).

    DATABASE_URL is pointed at it so Alembic's env.py and Settings() both follow.
    """
    base = make_url(Settings().database_url)
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {TEST_DB}"))
    admin.dispose()
    url = base.set(database=TEST_DB).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    return url


@pytest.fixture
def sessions(test_db_url: str) -> Iterator[sessionmaker[Session]]:
    command.upgrade(Config("alembic.ini"), "head")
    engine = make_engine(test_db_url)
    yield make_session_factory(engine)
    engine.dispose()
