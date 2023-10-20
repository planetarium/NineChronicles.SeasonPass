from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from season_pass import settings

engine = create_engine(settings.DB_URI, echo=settings.DB_ECHO)


def session():
    sess = scoped_session(sessionmaker(engine))
    try:
        yield sess
    finally:
        sess.close()
