from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import config

engine = create_engine(
    str(config.pg_dsn),
    echo=config.db_echo,
    # Raised from 10+20=30. postgres max_connections=10000 (plenty of headroom),
    # and 30 was exhausting under bursts + connections held idle-in-transaction
    # across slow RPC calls -> QueuePool timeouts on every DB endpoint. The extra
    # headroom absorbs those holds (which idle_in_transaction_session_timeout /
    # the GQL-out-of-transaction refactor then reclaim/eliminate).
    pool_size=30,
    max_overflow=30,
    pool_timeout=10,  # fail fast on a DB stall instead of pinning a worker thread for 60s
    pool_recycle=3600,
    pool_pre_ping=True,
)


SessionLocal = sessionmaker(engine)


def session():
    # Plain Session, not scoped_session: FastAPI runs the dependency setup, the
    # sync handler and the teardown in different threadpool threads, and a
    # scoped_session keeps one Session per *thread*. close() in teardown then
    # closed a different (empty) Session and the handler's connection stayed
    # checked out idle-in-transaction until GC -> QueuePool exhaustion.
    sess = SessionLocal()
    try:
        yield sess
    finally:
        sess.close()
