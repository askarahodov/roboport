"""SQLAlchemy domain mapping for the stable PostgreSQL baseline.

Alembic's raw-SQL migrations remain authoritative for trigger and CHECK DDL.
This declarative mapping is intentionally restricted to core tables and does
not call create_all; runtime operations retain their explicit transactional SQL.
"""
from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from uuid import UUID as UUIDValue


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customer"
    id: Mapped[UUIDValue] = mapped_column(UUID(as_uuid=True), primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)


class RobotDevice(Base):
    __tablename__ = "robot_device"
    id: Mapped[UUIDValue] = mapped_column(UUID(as_uuid=True), primary_key=True)
    serial_number: Mapped[str | None] = mapped_column(Text, unique=True)


class ServiceTicket(Base):
    __tablename__ = "service_ticket"
    id: Mapped[UUIDValue] = mapped_column(UUID(as_uuid=True), primary_key=True)
    customer_id: Mapped[UUIDValue] = mapped_column(UUID(as_uuid=True), ForeignKey("customer.id"), nullable=False)
    device_id: Mapped[UUIDValue | None] = mapped_column(UUID(as_uuid=True), ForeignKey("robot_device.id"))
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="DRAFT")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Estimate(Base):
    __tablename__ = "estimate"
    __table_args__ = (UniqueConstraint("ticket_id", "version"),)
    id: Mapped[UUIDValue] = mapped_column(UUID(as_uuid=True), primary_key=True)
    ticket_id: Mapped[UUIDValue] = mapped_column(UUID(as_uuid=True), ForeignKey("service_ticket.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="DRAFT")
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EstimateLine(Base):
    __tablename__ = "estimate_line"
    id: Mapped[UUIDValue] = mapped_column(UUID(as_uuid=True), primary_key=True)
    estimate_id: Mapped[UUIDValue] = mapped_column(UUID(as_uuid=True), ForeignKey("estimate.id"), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)


class StockLot(Base):
    __tablename__ = "stock_lot"
    id: Mapped[UUIDValue] = mapped_column(UUID(as_uuid=True), primary_key=True)
    part_number: Mapped[str] = mapped_column(Text, nullable=False)
    available_qty: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_qty: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
