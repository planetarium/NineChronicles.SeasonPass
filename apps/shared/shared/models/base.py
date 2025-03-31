import sqlalchemy as sa
from sqlalchemy import Column, DateTime, Integer, TypeDecorator, func
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class AutoIdMixin:
    id = Column(Integer, primary_key=True, autoincrement=True)


class TimeStampMixin:
    # https://spoqa.github.io/2019/02/15/python-timezone.html
    created_at = Column(DateTime(timezone=True), default=func.now())
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now())


class EnumType(TypeDecorator):
    impl = sa.Integer

    def __init__(self, enum_cls, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enum_cls = enum_cls

    def process_bind_param(self, value, dialect):
        if value is not None:
            if isinstance(value, self.enum_cls):
                return value.value
            else:
                raise ValueError(f"{value!r} is not a value {self.enum_cls}")

    def process_result_value(self, value, dialect):
        if value is not None:
            return self.enum_cls(value)
