from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, Optional

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


DEFAULT_DB_PATH = os.getenv(
    "DATABASE_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "payguard.db"),
)


class Base(DeclarativeBase):
    pass


class WebhookEvent(Base):
    """
    SQLite SQLAlchemy model for persisted webhook events and Dead-Letter Queue tracking.
    """

    __tablename__ = "webhook_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(255), unique=True, nullable=False, index=True)
    provider = Column(String(64), nullable=False, index=True)
    event_type = Column(String(128), nullable=False)
    payload = Column(Text, nullable=False)
    signature_valid = Column(Boolean, nullable=False, default=False)
    status = Column(String(32), nullable=False, default="pending", index=True)
    retry_count = Column(Integer, nullable=False, default=0)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def get_payload_dict(self) -> Dict[str, Any]:
        if isinstance(self.payload, dict):
            return self.payload
        try:
            return json.loads(self.payload) if self.payload else {}
        except Exception:
            return {"raw": self.payload}

    def to_dict(self) -> Dict[str, Any]:
        payload_obj = self.get_payload_dict()
        data_section = (
            payload_obj.get("data")
            if isinstance(payload_obj.get("data"), dict)
            else {}
        )
        customer_section = (
            data_section.get("customer")
            if isinstance(data_section.get("customer"), dict)
            else {}
        )

        # Extract display helpers for the dashboard while preserving core fields
        raw_amount = data_section.get("amount", 0)
        try:
            amount_val = float(raw_amount)
            if self.provider == "paystack" and amount_val >= 100:
                amount_val = round(amount_val / 100.0, 2)
        except (TypeError, ValueError):
            amount_val = 0.0

        created_iso = (
            self.created_at.isoformat()
            if isinstance(self.created_at, datetime)
            else str(self.created_at)
        )

        return {
            "id": self.id,
            "event_id": self.event_id,
            "provider": self.provider,
            "event_type": self.event_type,
            "payload": payload_obj,
            "signature_valid": bool(self.signature_valid),
            "status": self.status,
            "retry_count": int(self.retry_count or 0),
            "created_at": created_iso,
            # Convenience metadata for UI display
            "reference": data_section.get("reference")
            or data_section.get("tx_ref")
            or self.event_id,
            "amount": amount_val,
            "currency": data_section.get("currency", "NGN"),
            "customer_email": customer_section.get("email"),
        }


_engines: Dict[str, Any] = {}
_session_factories: Dict[str, sessionmaker] = {}


def get_database_url(db_path: Optional[str] = None) -> str:
    resolved = db_path or os.getenv("DATABASE_PATH", DEFAULT_DB_PATH)
    if resolved.startswith("sqlite://"):
        return resolved
    return f"sqlite:///{resolved}"


def get_engine(db_path: Optional[str] = None):
    db_url = get_database_url(db_path)
    if db_url not in _engines:
        _engines[db_url] = create_engine(
            db_url,
            connect_args={"check_same_thread": False},
            future=True,
        )
    return _engines[db_url]


def init_db(db_path: Optional[str] = None) -> None:
    engine = get_engine(db_path)
    Base.metadata.create_all(bind=engine)


def get_session(db_path: Optional[str] = None) -> Session:
    db_url = get_database_url(db_path)
    if db_url not in _session_factories:
        engine = get_engine(db_path)
        Base.metadata.create_all(bind=engine)
        _session_factories[db_url] = sessionmaker(
            bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
        )
    return _session_factories[db_url]()
