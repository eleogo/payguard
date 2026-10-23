from datetime import datetime, timezone
import hashlib
import json
import os
from typing import Any, Callable, Dict, Literal, Optional, Tuple
import uuid

from flask import Flask, jsonify, request
from flask_cors import CORS
from pydantic import BaseModel, Field, ValidationError
import requests

from database import DEFAULT_DB_PATH, WebhookEvent, get_session, init_db
from verifier import (
    DEFAULT_FLUTTERWAVE_SECRET,
    DEFAULT_WEBHOOK_SECRET,
    compute_hmac_sha512,
    verify_webhook_signature,
)


class SimulatePayloadSchema(BaseModel):
    provider: Literal["paystack", "flutterwave"] = "paystack"
    event_type: str = Field(default="charge.success")
    amount: float = Field(default=25000.0, gt=0)
    currency: str = Field(default="NGN")
    customer_email: str = Field(default="adaeze@fintech.ng")
    event_id: Optional[str] = None
    test_mode: bool = False
    simulate_failure: bool = False
    simulate_duplicate: bool = False


class ConfigUpdateSchema(BaseModel):
    target_url: Optional[str] = None
    test_mode: Optional[bool] = None
    webhook_secret: Optional[str] = None


def extract_event_id_and_type(
    provider: str, payload: Dict[str, Any], raw_body: bytes
) -> Tuple[str, str]:
    """
    Extracts a unique `event_id` (for idempotency) and `event_type` from a
    Paystack or Flutterwave webhook payload.
    """
    data_obj = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    event_type = str(
        payload.get("event")
        or payload.get("event.type")
        or "charge.success"
    )

    candidate_id = (
        payload.get("event_id")
        or data_obj.get("reference")
        or data_obj.get("tx_ref")
        or data_obj.get("flw_ref")
        or data_obj.get("id")
        or payload.get("id")
    )

    if candidate_id:
        candidate_str = str(candidate_id)
        prefix = f"{provider.lower()}_"
        event_id = (
            candidate_str
            if candidate_str.lower().startswith(prefix)
            else f"{prefix}{candidate_str}"
        )
    else:
        digest = hashlib.sha256(raw_body).hexdigest()[:20]
        event_id = f"{provider.lower()}_{digest}"

    return event_id, event_type


def forward_to_target(
    target_url: str,
    event: WebhookEvent,
    custom_sender: Optional[Callable[[str, Dict[str, Any], Dict[str, str]], int]] = None,
) -> bool:
    """
    Forwards the verified webhook payload to the configured target URL.
    Returns True if downstream returns a 2xx HTTP status, False otherwise.
    """
    payload_dict = event.get_payload_dict()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "PayGuard-Webhook-Gateway/1.0",
        "X-PayGuard-Event-Id": event.event_id,
        "X-PayGuard-Provider": event.provider,
        "X-PayGuard-Event-Type": event.event_type,
        "X-PayGuard-Retry-Count": str(event.retry_count),
    }

    if custom_sender is not None:
        try:
            status_code = custom_sender(target_url, payload_dict, headers)
            return 200 <= status_code < 300
        except Exception:
            return False

    normalized = (target_url or "").rstrip("/").lower()
    if normalized.endswith("/api/mock-merchant/success"):
        return True
    if normalized.endswith("/api/mock-merchant/down") or normalized.endswith(
        "/api/mock-merchant/fail"
    ):
        return False
    if normalized.endswith("/api/mock-merchant/flaky"):
        return int(event.retry_count or 0) >= 1

    try:
        response = requests.post(
            target_url,
            json=payload_dict,
            headers=headers,
            timeout=5,
        )
        return 200 <= response.status_code < 300
    except Exception:
        return False


def build_mock_payload(sim: SimulatePayloadSchema) -> Dict[str, Any]:
    """Generates a realistic mock Paystack or Flutterwave charge.success payload."""
    unique_suffix = uuid.uuid4().hex[:8].upper()
    if sim.provider == "paystack":
        ref = sim.event_id or f"PSK_{unique_suffix}"
        return {
            "event": sim.event_type,
            "data": {
                "id": int(hashlib.sha256(ref.encode()).hexdigest()[:7], 16),
                "domain": "test",
                "status": "success",
                "reference": ref,
                "amount": int(round(sim.amount * 100)),  # Paystack uses minor units (kobo)
                "currency": sim.currency.upper(),
                "channel": "bank_transfer",
                "paid_at": datetime.now(timezone.utc).isoformat(),
                "customer": {
                    "id": 91823,
                    "email": sim.customer_email,
                    "customer_code": "CUS_payguard_demo",
                },
            },
        }

    ref = sim.event_id or f"FLW_{unique_suffix}"
    return {
        "event": sim.event_type,
        "data": {
            "id": int(hashlib.sha256(ref.encode()).hexdigest()[:7], 16),
            "tx_ref": ref,
            "flw_ref": f"FLW-MOMO-{unique_suffix}",
            "amount": sim.amount,
            "currency": sim.currency.upper(),
            "charged_amount": sim.amount,
            "status": "successful",
            "payment_type": "mobilemoney",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "customer": {
                "id": 44109,
                "name": "Kwame Mensah",
                "email": sim.customer_email,
            },
        },
    }


def create_app(test_config: Optional[Dict[str, Any]] = None) -> Flask:
    app = Flask(__name__)
    CORS(app)

    app.config["DATABASE_PATH"] = os.getenv("DATABASE_PATH", DEFAULT_DB_PATH)
    app.config["WEBHOOK_SECRET"] = os.getenv(
        "WEBHOOK_SECRET", DEFAULT_WEBHOOK_SECRET
    )
    app.config["PAYSTACK_SECRET"] = os.getenv(
        "PAYSTACK_SECRET_KEY", app.config["WEBHOOK_SECRET"]
    )
    app.config["FLUTTERWAVE_SECRET"] = os.getenv(
        "FLUTTERWAVE_SECRET_HASH", DEFAULT_FLUTTERWAVE_SECRET
    )
    app.config["TARGET_URL"] = os.getenv(
        "TARGET_WEBHOOK_URL", "http://localhost:5000/api/mock-merchant/success"
    )
    app.config["TEST_MODE"] = os.getenv("TEST_MODE", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    app.config["DUPLICATES_BLOCKED"] = 0

    if test_config:
        app.config.update(test_config)

    init_db(app.config["DATABASE_PATH"])

    if not app.config.get("TESTING", False):
        _seed_demo_events_if_empty(app.config["DATABASE_PATH"])
        app.config["DUPLICATES_BLOCKED"] = 1

    @app.route("/api/health", methods=["GET"])
    def health():
        return jsonify(
            {
                "status": "healthy",
                "service": "PayGuard Webhook Gateway & DLQ",
                "test_mode": bool(app.config.get("TEST_MODE", False)),
                "target_url": app.config.get("TARGET_URL"),
                "supported_providers": ["paystack", "flutterwave"],
            }
        )

    @app.route("/webhooks/<provider>", methods=["POST"])
    def receive_webhook(provider: str):
        """
        1. Checks provider validity (paystack / flutterwave).
        2. Parses payload and computes/extracts `event_id` for idempotency.
        3. Ignores duplicate events if `event_id` already exists in SQLite.
        4. Verifies HMAC-SHA512 signature (or passes if `test_mode` is active).
        5. Logs event to SQLite (`WebhookEvent`) and forwards to `TARGET_URL`.
        """
        provider_clean = provider.strip().lower()
        if provider_clean not in ("paystack", "flutterwave"):
            return (
                jsonify(
                    {
                        "error": "UNSUPPORTED_PROVIDER",
                        "message": f"Unsupported provider '{provider}'. Supported: paystack, flutterwave",
                    }
                ),
                400,
            )

        raw_body = request.get_data()
        try:
            payload_dict = json.loads(raw_body.decode("utf-8") or "{}")
            if not isinstance(payload_dict, dict):
                raise ValueError("Payload must be a JSON object")
        except Exception:
            return (
                jsonify(
                    {
                        "error": "INVALID_JSON",
                        "message": "Request body must be a valid JSON object",
                    }
                ),
                400,
            )

        event_id, event_type = extract_event_id_and_type(
            provider_clean, payload_dict, raw_body
        )

        session = get_session(app.config["DATABASE_PATH"])
        try:
            # 1. Idempotency check: ignore duplicates
            existing = (
                session.query(WebhookEvent)
                .filter(WebhookEvent.event_id == event_id)
                .first()
            )
            if existing is not None:
                app.config["DUPLICATES_BLOCKED"] = (
                    int(app.config.get("DUPLICATES_BLOCKED", 0)) + 1
                )
                return (
                    jsonify(
                        {
                            "status": "duplicate_ignored",
                            "duplicate": True,
                            "duplicates_blocked": app.config["DUPLICATES_BLOCKED"],
                            "message": f"Event '{event_id}' already processed; duplicate ignored.",
                            "event": existing.to_dict(),
                        }
                    ),
                    200,
                )

            # 2. Verify cryptographic signature (or allow via test-mode flag)
            secret = (
                app.config["PAYSTACK_SECRET"]
                if provider_clean == "paystack"
                else app.config["FLUTTERWAVE_SECRET"]
            )
            query_test_mode = request.args.get("test_mode", "").lower() in (
                "1",
                "true",
                "yes",
            )
            effective_test_mode = (
                True
                if (app.config.get("TEST_MODE", False) or query_test_mode)
                else None
            )

            sig_valid = verify_webhook_signature(
                provider=provider_clean,
                raw_body=raw_body,
                headers=dict(request.headers),
                secret=secret,
                test_mode=effective_test_mode,
            )

            if not sig_valid:
                failed_event = WebhookEvent(
                    event_id=event_id,
                    provider=provider_clean,
                    event_type=event_type,
                    payload=json.dumps(payload_dict),
                    signature_valid=False,
                    status="failed",
                    retry_count=0,
                )
                session.add(failed_event)
                session.commit()
                session.refresh(failed_event)
                return (
                    jsonify(
                        {
                            "status": "failed",
                            "error": "INVALID_SIGNATURE",
                            "message": f"Invalid {provider_clean} webhook signature",
                            "event": failed_event.to_dict(),
                        }
                    ),
                    401,
                )

            # 3. Log verified webhook as 'pending' in SQLite
            new_event = WebhookEvent(
                event_id=event_id,
                provider=provider_clean,
                event_type=event_type,
                payload=json.dumps(payload_dict),
                signature_valid=True,
                status="pending",
                retry_count=0,
            )
            session.add(new_event)
            session.commit()
            session.refresh(new_event)

            # 4. Forward to target URL
            target_url = app.config.get("TARGET_URL")
            custom_sender = app.config.get("CUSTOM_SENDER")
            delivered = forward_to_target(
                target_url=target_url,
                event=new_event,
                custom_sender=custom_sender,
            )

            new_event.status = "processed" if delivered else "failed"
            session.commit()
            session.refresh(new_event)

            return (
                jsonify(
                    {
                        "status": new_event.status,
                        "duplicate": False,
                        "event": new_event.to_dict(),
                    }
                ),
                200,
            )
        finally:
            session.close()

    @app.route("/api/events", methods=["GET"])
    def list_events():
        """
        Returns all logged webhook events with optional filtering by `status`
        (processed/failed/pending) and `provider` (paystack/flutterwave),
        along with top-level summary metrics.
        """
        status_filter = request.args.get("status")
        provider_filter = request.args.get("provider")
        search_query = request.args.get("search", "").strip()

        session = get_session(app.config["DATABASE_PATH"])
        try:
            all_rows = session.query(WebhookEvent).all()
            metrics = {
                "total": len(all_rows),
                "processed": sum(1 for r in all_rows if r.status == "processed"),
                "failed": sum(1 for r in all_rows if r.status == "failed"),
                "pending": sum(1 for r in all_rows if r.status == "pending"),
                "duplicates_blocked": int(app.config.get("DUPLICATES_BLOCKED", 0)),
            }

            query = session.query(WebhookEvent)
            if status_filter and status_filter.lower() != "all":
                query = query.filter(
                    WebhookEvent.status == status_filter.strip().lower()
                )
            if provider_filter and provider_filter.lower() != "all":
                query = query.filter(
                    WebhookEvent.provider == provider_filter.strip().lower()
                )
            if search_query:
                like_pattern = f"%{search_query}%"
                query = query.filter(
                    (WebhookEvent.event_id.ilike(like_pattern))
                    | (WebhookEvent.event_type.ilike(like_pattern))
                    | (WebhookEvent.payload.ilike(like_pattern))
                )

            events = query.order_by(
                WebhookEvent.created_at.desc(), WebhookEvent.id.desc()
            ).all()
            serialized = [ev.to_dict() for ev in events]
            return jsonify(
                {
                    "events": serialized,
                    "total": len(serialized),
                    "metrics": metrics,
                }
            )
        finally:
            session.close()

    @app.route("/api/events/<int:event_pk>", methods=["GET"])
    def get_single_event(event_pk: int):
        session = get_session(app.config["DATABASE_PATH"])
        try:
            ev = (
                session.query(WebhookEvent)
                .filter(WebhookEvent.id == event_pk)
                .first()
            )
            if not ev:
                return jsonify({"error": "NOT_FOUND", "message": "Event not found"}), 404
            return jsonify(ev.to_dict())
        finally:
            session.close()

    @app.route("/api/events/<int:event_pk>/replay", methods=["POST"])
    def replay_event(event_pk: int):
        """
        Retries sending a failed webhook event and increments `retry_count`.
        """
        session = get_session(app.config["DATABASE_PATH"])
        try:
            ev = (
                session.query(WebhookEvent)
                .filter(WebhookEvent.id == event_pk)
                .first()
            )
            if not ev:
                return (
                    jsonify(
                        {
                            "error": "NOT_FOUND",
                            "message": f"WebhookEvent with id={event_pk} not found",
                        }
                    ),
                    404,
                )

            body = request.get_json(silent=True) or {}
            override_target = body.get("target_url") or app.config.get("TARGET_URL")

            ev.retry_count = int(ev.retry_count or 0) + 1
            custom_sender = app.config.get("CUSTOM_SENDER")
            delivered = forward_to_target(
                target_url=override_target,
                event=ev,
                custom_sender=custom_sender,
            )
            ev.status = "processed" if delivered else "failed"
            session.commit()
            session.refresh(ev)

            return (
                jsonify(
                    {
                        "message": f"Replay attempt #{ev.retry_count} completed with status '{ev.status}'",
                        "status": ev.status,
                        "retry_count": ev.retry_count,
                        "event": ev.to_dict(),
                    }
                ),
                200,
            )
        finally:
            session.close()

    @app.route("/api/simulate", methods=["POST"])
    def simulate_webhook():
        """
        Generates a realistic mock Paystack or Flutterwave `charge.success`
        payload for local testing, signs it (or uses test_mode), and dispatches
        it through `/webhooks/<provider>`.
        """
        body = request.get_json(silent=True) or {}
        try:
            sim = SimulatePayloadSchema(**body)
        except ValidationError as exc:
            return jsonify({"error": "VALIDATION_ERROR", "details": exc.errors()}), 400

        if sim.simulate_duplicate and not sim.event_id:
            session = get_session(app.config["DATABASE_PATH"])
            try:
                latest = (
                    session.query(WebhookEvent)
                    .filter(WebhookEvent.provider == sim.provider.lower())
                    .order_by(WebhookEvent.id.desc())
                    .first()
                )
                if latest:
                    sim.event_id = latest.event_id
            finally:
                session.close()

        mock_payload = build_mock_payload(sim)
        raw_bytes = json.dumps(mock_payload, separators=(",", ":")).encode("utf-8")

        provider_clean = sim.provider.lower()
        secret = (
            app.config["PAYSTACK_SECRET"]
            if provider_clean == "paystack"
            else app.config["FLUTTERWAVE_SECRET"]
        )
        signature = compute_hmac_sha512(raw_bytes, secret)

        headers = {"Content-Type": "application/json"}
        if provider_clean == "paystack":
            headers["x-paystack-signature"] = signature
        else:
            headers["verif-hash"] = signature

        if sim.test_mode:
            headers["X-PayGuard-Test-Mode"] = "true"

        previous_target = app.config.get("TARGET_URL")
        if sim.simulate_failure:
            app.config["TARGET_URL"] = "http://localhost:5000/api/mock-merchant/down"

        try:
            with app.test_request_context(
                f"/webhooks/{provider_clean}",
                method="POST",
                data=raw_bytes,
                headers=headers,
            ):
                resp, status_code = receive_webhook(provider_clean)
                resp_data = resp.get_json()
                resp_data["generated_payload"] = mock_payload
                resp_data["signature"] = signature
                return jsonify(resp_data), status_code
        finally:
            if sim.simulate_failure:
                app.config["TARGET_URL"] = previous_target

    @app.route("/api/config", methods=["GET", "PUT"])
    def manage_config():
        if request.method == "GET":
            return jsonify(
                {
                    "target_url": app.config.get("TARGET_URL"),
                    "test_mode": bool(app.config.get("TEST_MODE", False)),
                }
            )
        body = request.get_json(silent=True) or {}
        try:
            cfg = ConfigUpdateSchema(**body)
        except ValidationError as exc:
            return jsonify({"error": "VALIDATION_ERROR", "details": exc.errors()}), 400

        if cfg.target_url is not None:
            app.config["TARGET_URL"] = cfg.target_url
        if cfg.test_mode is not None:
            app.config["TEST_MODE"] = cfg.test_mode
        if cfg.webhook_secret is not None:
            app.config["WEBHOOK_SECRET"] = cfg.webhook_secret
            app.config["PAYSTACK_SECRET"] = cfg.webhook_secret
            app.config["FLUTTERWAVE_SECRET"] = cfg.webhook_secret

        return jsonify(
            {
                "target_url": app.config.get("TARGET_URL"),
                "test_mode": bool(app.config.get("TEST_MODE", False)),
            }
        )

    @app.route("/api/mock-merchant/success", methods=["POST"])
    def mock_merchant_success():
        return jsonify({"status": "ok", "message": "Wallet credited"}), 200

    @app.route("/api/mock-merchant/down", methods=["POST"])
    def mock_merchant_down():
        return jsonify({"error": "Downstream outage"}), 503

    return app


def _seed_demo_events_if_empty(db_path: str) -> None:
    session = get_session(db_path)
    try:
        if session.query(WebhookEvent).count() > 0:
            return

        samples = [
            WebhookEvent(
                event_id="paystack_PSK_NGN_884210",
                provider="paystack",
                event_type="charge.success",
                payload=json.dumps(
                    {
                        "event": "charge.success",
                        "data": {
                            "id": 9823411,
                            "reference": "PSK_NGN_884210",
                            "amount": 4500000,
                            "currency": "NGN",
                            "status": "success",
                            "customer": {"email": "adaeze.obi@kuda-demo.ng"},
                        },
                    }
                ),
                signature_valid=True,
                status="processed",
                retry_count=0,
            ),
            WebhookEvent(
                event_id="flutterwave_FLW_MPESA_55109",
                provider="flutterwave",
                event_type="charge.success",
                payload=json.dumps(
                    {
                        "event": "charge.success",
                        "data": {
                            "id": 7719204,
                            "tx_ref": "FLW_MPESA_55109",
                            "amount": 12500,
                            "currency": "KES",
                            "status": "successful",
                            "customer": {"email": "wanjiku@mpesa-merchant.ke"},
                        },
                    }
                ),
                signature_valid=True,
                status="processed",
                retry_count=0,
            ),
            WebhookEvent(
                event_id="paystack_PSK_DLQ_901",
                provider="paystack",
                event_type="charge.success",
                payload=json.dumps(
                    {
                        "event": "charge.success",
                        "data": {
                            "id": 9823990,
                            "reference": "PSK_DLQ_901",
                            "amount": 18000000,
                            "currency": "NGN",
                            "status": "success",
                            "customer": {"email": "treasury@moniepoint-demo.ng"},
                        },
                    }
                ),
                signature_valid=True,
                status="failed",
                retry_count=2,
            ),
            WebhookEvent(
                event_id="flutterwave_FLW_MOMO_GH_402",
                provider="flutterwave",
                event_type="charge.success",
                payload=json.dumps(
                    {
                        "event": "charge.success",
                        "data": {
                            "id": 7719882,
                            "tx_ref": "FLW_MOMO_GH_402",
                            "amount": 3400,
                            "currency": "GHS",
                            "status": "successful",
                            "customer": {"email": "kofi.mensah@accra-pay.gh"},
                        },
                    }
                ),
                signature_valid=True,
                status="failed",
                retry_count=1,
            ),
        ]
        session.add_all(samples)
        session.commit()
    finally:
        session.close()


if __name__ == "__main__":
    app = create_app()
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
