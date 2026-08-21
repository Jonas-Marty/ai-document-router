from collections.abc import Generator
from pathlib import Path
from sqlite3 import Connection

from sqlalchemy import event
from sqlalchemy.pool import ConnectionPoolEntry
from sqlmodel import Session, create_engine

from app.config import settings

_SQLITE_PREFIX = "sqlite:///"


def _ensure_sqlite_dir(database_url: str) -> None:
    if not database_url.startswith(_SQLITE_PREFIX):
        return
    path = database_url[len(_SQLITE_PREFIX) :]
    if path == ":memory:":
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_dir(settings.database_url)

engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(
    dbapi_connection: Connection, _connection_record: ConnectionPoolEntry
) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
