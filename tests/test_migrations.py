import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from beantracker.models.orm import Base

pytestmark = pytest.mark.integration


def test_migrations_match_models(test_db_url: str) -> None:
    """Upgrading an empty DB must yield exactly the schema the ORM models describe."""
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(test_db_url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []

    command.downgrade(Config("alembic.ini"), "base")
    engine.dispose()
