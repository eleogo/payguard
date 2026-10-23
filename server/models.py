from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator


WebhookStatus = Literal["pending", "delivered", "retrying", "dlq", "rejected"]


class NormalizedWebhookMeta(BaseModel):
    provider: str = Field(..., min_length=2, max_length=64)
    event_type: str = Field(default="unknown.event", max_length=128)
    external_event_id: Optional[str] = Field(default=None, max_length=256)
    reference: Optional[str] = Field(default=None, max_length=256)
    amount: Optional[float] = Field(default=0.0, ge=0.0)
    currency: str = Field(default="NGN", max_length=12)
    customer_email: Optional[str] = Field(default=None, max_length=256)

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return (value or "NGN").strip().upper()


class DeliveryLogModel(BaseModel):
    id: int
    webhook_id: str
    attempt_number: int
    target_url: str
    status_code: Optional[int] = None
    response_body: Optional[str] = None
    error_message: Optional[str] = None
    duration_ms: float = 0.0
    success: bool = False
    created_at: str


class WebhookEventModel(BaseModel):
    id: str
    provider: str
    event_type: str
    external_event_id: Optional[str] = None
    idempotency_key: str
    reference: Optional[str] = None
    amount: float = 0.0
    currency: str = "NGN"
    customer_email: Optional[str] = None
    payload: Dict[str, Any]
    headers: Dict[str, str]
    signature_valid: bool
    status: WebhookStatus
    attempts: int = 0
    max_attempts: int = 3
    next_retry_at: Optional[str] = None
    last_error: Optional[str] = None
    last_response_code: Optional[int] = None
    last_response_body: Optional[str] = None
    target_url: str
    created_at: str
    updated_at: str
    delivered_at: Optional[str] = None
    delivery_logs: Optional[List[DeliveryLogModel]] = None


class SimulateWebhookRequest(BaseModel):
    provider: Literal["paystack", "flutterwave"] = "paystack"
    event_type: str = Field(default="charge.success", min_length=2, max_length=128)
    amount: float = Field(default=25000.0, gt=0)
    currency: Literal["NGN", "GHS", "KES", "ZAR", "USD", "UGX", "RWF"] = "NGN"
    customer_email: str = Field(default="amara.okafor@fintech.ng")
    reference: Optional[str] = None
    tamper_signature: bool = False
    duplicate_reference: bool = False


class ConfigUpdateRequest(BaseModel):
    target_webhook_url: Optional[str] = Field(default=None, min_length=8)
    max_retry_attempts: Optional[int] = Field(default=None, ge=1, le=10)
    base_retry_delay_seconds: Optional[int] = Field(default=None, ge=1, le=300)


class ReplayRequest(BaseModel):
    override_target_url: Optional[str] = None
