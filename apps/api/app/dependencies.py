from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from app.config import config

engine = create_engine(
    str(config.pg_dsn),
    echo=config.db_echo,
    pool_size=10,  
    max_overflow=20,  
    pool_timeout=60,  
    pool_recycle=3600, 
    pool_pre_ping=True  
)


def session():
    sess = scoped_session(sessionmaker(engine))
    try:
        yield sess
    finally:
        sess.close()
