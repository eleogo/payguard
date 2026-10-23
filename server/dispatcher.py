from datetime import datetime, timedelta, timezone
import json
import random
import time
from typing import Any, Callable, Dict, Optional, Tuple
import requests
from config import settings
from database import get_due_retries, get_webhook_by_id, record_delivery_attempt


# Optional custom sender hook for unit testing
CustomSender = Callable[[str, Dict[str, Any], Dict[str, str], int], Tuple[int, str]]


def compute_backoff_seconds(attempt_number: int, base_delay: Optional[int] = None) -> int:
    base = base_delay if base_delay is not None else settings.base_retry_delay_seconds
    # Exponential backoff: base * 2^(attempt - 1)
    return int(base * (2 ** max(0, attempt_number - 1)))


def _execute_BuiltIn_mock_merchant(
    target_url: str, payload: Dict[str, Any], attempt_number: int
) -> Optional[Tuple[int, str, float]]:
    """
    Fast built-in simulation for PayGuard's built-in mock merchant endpoints
    (/api/mock-merchant/success, /api/mock-merchant/flaky, /api/mock-merchant/down)
    so single-threaded dev servers or pytest runs never deadlock on loopback HTTP calls.
    """
    normalized = target_url.rstrip("/").lower()
    start = time.perf_counter()
    if normalized.endswith("/api/mock-merchant/success"):
        duration_ms = (time.perf_counter() - start) * 1000.0 + 12.4
        body = json.dumps(
            {
                "status": "credited",
                "service": "core-wallet-ledger",
                "message": "Customer wallet credited idempotently",
                "attempt": attempt_number,
            }
        )
        return 200, body, duration_ms

    if normalized.endswith("/api/mock-merchant/down"):
        duration_ms = (time.perf_counter() - start) * 1000.0 + 48.2
        body = json.dumps(
            {
                "error": "SERVICE_UNAVAILABLE",
                "message": "Downstream database connection pool exhausted (Simulated Outage)",
                "attempt": attempt_number,
            }
        )
        return 503, body, duration_ms

    if normalized.endswith("/api/mock-merchant/flaky"):
        duration_ms = (time.perf_counter() - start) * 1000.0 + 29.7
        # Succeeds on attempt >= 2 or randomly 50% on attempt 1
        if attempt_number >= 2 or random.random() > 0.65:
            body = json.dumps(
                {
                    "status": "credited_after_retry",
                    "service": "core-wallet-ledger",
                    "attempt": attempt_number,
                }
            )
            return 200, body, duration_ms
        body = json.dumps(
            {
                "error": "GATEWAY_TIMEOUT",
                "message": "Downstream ledger lock timeout (Flaky endpoint failed on attempt 1)",
                "attempt": attempt_number,
            }
        )
        return 502, body, duration_ms

    return None


def dispatch_webhook(
    db_path: str,
    webhook_id: str,
    override_target_url: Optional[str] = None,
    custom_sender: Optional[CustomSender] = None,
    force_exhaust_to_dlq: bool = False,
) -> Dict[str, Any]:
    """
    Forward a stored webhook event to the target merchant endpoint.
    If delivery fails:
      - If attempts < max_attempts (and not force_exhaust_to_dlq), status -> 'retrying'
        with exponential backoff (`next_retry_at`).
      - If attempts >= max_attempts, status -> 'dlq' (Dead-Letter Queue).
    """
    webhook = get_webhook_by_id(db_path, webhook_id, include_logs=False)
    if not webhook:
        raise ValueError(f"Webhook {webhook_id} not found")

    target_url = override_target_url or webhook["target_url"] or settings.target_webhook_url
    attempt_number = int(webhook["attempts"]) + 1
    max_attempts = int(webhook["max_attempts"] or settings.max_retry_attempts)

    forward_headers = {
        "Content-Type": "application/json",
        "User-Agent": "PayGuard-Gateway/1.0 (+https://github.com/payguard/payguard)",
        "X-PayGuard-Webhook-Id": webhook["id"],
        "X-PayGuard-Provider": webhook["provider"],
        "X-PayGuard-Event": webhook["event_type"],
        "X-PayGuard-Idempotency-Key": webhook["idempotency_key"],
        "X-PayGuard-Attempt": str(attempt_number),
    }

    status_code: Optional[int] = None
    response_body: Optional[str] = None
    error_message: Optional[str] = None
    success = False
    duration_ms = 0.0

    try:
        if custom_sender is not None:
            start = time.perf_counter()
            status_code, response_body = custom_sender(
                target_url, webhook["payload"], forward_headers, attempt_number
            )
            duration_ms = (time.perf_counter() - start) * 1000.0
        else:
            mock_result = _execute_BuiltIn_mock_merchant(
                target_url, webhook["payload"], attempt_number
            )
            if mock_result is not None:
                status_code, response_body, duration_ms = mock_result
            else:
                start = time.perf_counter()
                resp = requests.post(
                    target_url,
                    json=webhook["payload"],
                    headers=forward_headers,
                    timeout=settings.forward_timeout_seconds,
                )
                duration_ms = (time.perf_counter() - start) * 1000.0
                status_code = resp.status_code
                response_body = resp.text[:2000] if resp.text else ""

        if status_code is not None and 200 <= status_code < 300:
            success = True
        else:
            error_message = f"HTTP {status_code}: Downstream endpoint returned non-2xx status"
    except Exception as exc:
        error_message = f"Delivery exception: {str(exc)}"

    if success:
        return record_delivery_attempt(
            db_path=db_path,
            webhook_id=webhook_id,
            attempt_number=attempt_number,
            target_url=target_url,
            status="delivered",
            status_code=status_code,
            response_body=response_body,
            error_message=None,
            duration_ms=duration_ms,
            success=True,
            next_retry_at=None,
        )

    # Determine whether to schedule retry or move to Dead-Letter Queue (DLQ)
    if attempt_number >= max_attempts or force_exhaust_to_dlq:
        new_status = "dlq"
        next_retry_at = None
    else:
        new_status = "retrying"
        backoff_sec = compute_backoff_seconds(attempt_number)
        next_retry_at = (
            datetime.now(timezone.utc) + timedelta(seconds=backoff_sec)
        ).isoformat()

    return record_delivery_attempt(
        db_path=db_path,
        webhook_id=webhook_id,
        attempt_number=attempt_number,
        target_url=target_url,
        status=new_status,
        status_code=status_code,
        response_body=response_body,
        error_message=error_message,
        duration_ms=duration_ms,
        success=False,
        next_retry_at=next_retry_at,
    )


def process_due_retries(
    db_path: str,
    now_iso: Optional[str] = None,
    custom_sender: Optional[CustomSender] = None,
) -> Dict[str, Any]:
    """Process all webhooks in `retrying` state whose `next_retry_at` is due."""
    due_items = get_due_retries(db_path, now_iso=now_iso)
    results = {
        "processed": len(due_items),
        "delivered": 0,
        "retrying": 0,
        "dlq": 0,
        "webhook_ids": [],
    }
    for item in due_items:
        updated = dispatch_webhook(
            db_path=db_path,
            webhook_id=item["id"],
            custom_sender=custom_sender,
        )
        results["webhook_ids"].append(updated["id"])
        st = updated["status"]
        if st in results:
            results[st] += 1
    return results
