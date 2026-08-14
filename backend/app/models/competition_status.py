"""
competition_status — lookup table. Design doc section 2.14.

Deliberately just two rows (active, completed) — the pending/declined/
cancelled outcomes live on competition_requests instead. A row here
only ever represents a competition that's live or finished; it never
represents a negotiation in progress.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import SmallInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .competition import Competition


class CompetitionStatus(Base):
    __tablename__ = "competition_status"

    status_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    status_name: Mapped[str] = mapped_column(unique=True, nullable=False)

    competitions: Mapped[list["Competition"]] = relationship(back_populates="status")

    def __repr__(self) -> str:
        return f"<CompetitionStatus {self.status_name!r}>"