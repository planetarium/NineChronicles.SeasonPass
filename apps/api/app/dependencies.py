from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from app.config import config

engine = create_engine(str(config.pg_dsn), echo=config.db_echo)


def session():
    sess = scoped_session(sessionmaker(engine))
    try:
        yield sess
    finally:
        sess.close()
