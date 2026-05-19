import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DB_PATH = os.environ.get("DB_PATH", "./data/rss.db")
if dirname := os.path.dirname(DB_PATH):
    os.makedirs(dirname, exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)

# Enable WAL mode for better concurrent read performance
@event.listens_for(engine, "connect")
def set_wal(dbapi_conn, _):
    dbapi_conn.execute("PRAGMA journal_mode=WAL")
    dbapi_conn.execute("PRAGMA foreign_keys=ON")
    # Wait up to 5s for the write lock instead of failing immediately when
    # the scheduler thread and a request thread contend.
    dbapi_conn.execute("PRAGMA busy_timeout=5000")

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
