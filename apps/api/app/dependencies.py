from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

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


def session():
    sess = scoped_session(sessionmaker(engine))
    try:
        yield sess
    finally:
        sess.close()
