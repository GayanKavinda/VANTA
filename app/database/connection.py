import logging
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.utils.constants import DB_PATH
from app.utils.logger import get_logger

log = get_logger("vanta.database")

DB_PATH.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    echo=False,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False},
)

_session_factory = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db():
    from app.database.models import DownloadRecord, SettingRecord

    inspector = inspect(engine)
    existing = set(inspector.get_table_names())

    expected = {DownloadRecord.__tablename__, SettingRecord.__tablename__}
    missing = expected - existing

    if missing:
        log.info("Creating tables: %s", missing)
        Base.metadata.create_all(engine, tables=[
            DownloadRecord.__table__,
            SettingRecord.__table__,
        ])

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_session() -> Session:
    return _session_factory()
