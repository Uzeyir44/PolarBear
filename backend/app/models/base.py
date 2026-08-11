"""
Shared declarative base. Every model in this package inherits from this
so they all register on the same MetaData — required for Alembic
autogenerate and for cross-table relationships to resolve.
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass