import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from beantracker.models.orm import Base

pytestmark = pytest.mark.integration


def test_migrations_match_models(test_db_url: str) -> None:
    """Upgrading an empty DB must yield exactly the schema the ORM models describe."""
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(test_db_url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []

    # The index cleanup: first_seen_run_id is indexed, and the three indexes that only
    # duplicated a unique constraint's leading column are gone.
    insp = inspect(engine)
    names = {i["name"] for t in ("coffee", "offer", "offer_snapshot") for i in insp.get_indexes(t)}
    assert "ix_coffee_first_seen_run_id" in names
    assert not names & {"ix_coffee_roaster_id", "ix_offer_coffee_id", "ix_offer_snapshot_offer_id"}

    command.downgrade(Config("alembic.ini"), "base")
    engine.dispose()
