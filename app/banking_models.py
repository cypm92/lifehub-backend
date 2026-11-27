"""Tablas bancarias aditivas; no alteran las tablas presupuestarias."""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint

from app.database import Base


class BankConnection(Base):
    __tablename__ = "bank_connections"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    bank_name = Column(String, nullable=False)
    bank_country = Column(String(2), nullable=False)
    session_encrypted = Column(String, nullable=False)
    valid_until = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BankAuthorization(Base):
    __tablename__ = "bank_authorizations"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    state_hash = Column(String(64), nullable=False, unique=True, index=True)
    bank_name = Column(String, nullable=False)
    bank_country = Column(String(2), nullable=False)
    connection_id = Column(Integer, ForeignKey("bank_connections.id", ondelete="CASCADE"), nullable=True)
    expires_at = Column(DateTime, nullable=False)
    consumed_at = Column(DateTime, nullable=True)


class BankAccount(Base):
    __tablename__ = "bank_accounts"
    __table_args__ = (UniqueConstraint("connection_id", "provider_uid", name="uq_bank_account_connection_uid"),)
    id = Column(Integer, primary_key=True)
    connection_id = Column(Integer, ForeignKey("bank_connections.id", ondelete="CASCADE"), nullable=False, index=True)
    provider_uid = Column(String, nullable=False)
    data = Column(JSON, nullable=False, default=dict)
    balances = Column(JSON, nullable=True)


class BankTransaction(Base):
    __tablename__ = "bank_transactions"
    __table_args__ = (UniqueConstraint("account_id", "dedupe_key", name="uq_bank_transaction_dedupe"),)
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("bank_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    dedupe_key = Column(String(128), nullable=False)
    raw_data = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BankAssignment(Base):
    __tablename__ = "bank_assignments"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    transaction_id = Column(Integer, ForeignKey("bank_transactions.id", ondelete="CASCADE"), nullable=False, unique=True)
    destination_type = Column(String, nullable=False)
    destination_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BankRule(Base):
    __tablename__ = "bank_rules"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    field = Column(String, nullable=False)
    contains = Column(String, nullable=False)
    destination_type = Column(String, nullable=False)
    destination_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
