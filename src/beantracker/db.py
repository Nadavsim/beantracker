from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from beantracker.config import Settings


def make_engine(url: str | None = None) -> Engine:
    return create_engine(url or Settings().database_url)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    # expire_on_commit=False: repo returns objects after commit without lazy reloads.
    return sessionmaker(engine, expire_on_commit=False)
