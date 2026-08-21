from sqlalchemy import text
from sqlmodel import Session

from app import db


def test_wal_and_foreign_keys_pragmas_are_set_on_connect() -> None:
    with Session(db.engine) as session:
        assert session.exec(text("PRAGMA journal_mode")).one()[0] == "wal"
        assert session.exec(text("PRAGMA foreign_keys")).one()[0] == 1
