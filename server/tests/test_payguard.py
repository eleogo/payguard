import json
import os
import tempfile
import pytest

from app import create_app
from database import WebhookEvent, get_session
from verifier import compute_hmac_sha512, verify_webhook_signature


TEST_SECRET = "sk_test_payguard_secret_key"


@pytest.fixture
def client():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    app = create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": db_path,
            "WEBHOOK_SECRET": TEST_SECRET,
            "PAYSTACK_SECRET": TEST_SECRET,
            "FLUTTERWAVE_SECRET": TEST_SECRET,
            "TARGET_URL": "http://localhost:5000/api/mock-merchant/success",
            "TEST_MODE": False,
        }
    )

    with app.test_client() as test_client:
        yield test_client

    try:
        os.remove(db_path)
    except OSError:
        pass


def test_hmac_signature_verification_paystack_and_flutterwave():
    """Unit test for HMAC-SHA512 signature verification across Paystack and Flutterwave."""
    raw_body = b'{"event":"charge.success","data":{"reference":"REF_HMAC_001","amount":250000}}'
    valid_sig = compute_hmac_sha512(raw_body, TEST_SECRET)

    # 1. Valid Paystack x-paystack-signature passes
    assert (
        verify_webhook_signature(
            "paystack",
            raw_body,
            {"x-paystack-signature": valid_sig},
            secret=TEST_SECRET,
            test_mode=False,
        )
        is True
    )

    # 2. Tampered Paystack signature fails
    assert (
        verify_webhook_signature(
            "paystack",
            raw_body,
            {"x-paystack-signature": "0000deadbeef_invalid_signature"},
            secret=TEST_SECRET,
            test_mode=False,
        )
        is False
    )

    # 3. Valid Flutterwave verif-hash passes
    assert (
        verify_webhook_signature(
            "flutterwave",
            raw_body,
            {"verif-hash": valid_sig},
            secret=TEST_SECRET,
            test_mode=False,
        )
        is True
    )

    # 4. Tampered Flutterwave verif-hash fails
    assert (
        verify_webhook_signature(
            "flutterwave",
            raw_body,
            {"verif-hash": "wrong_verif_hash"},
            secret=TEST_SECRET,
            test_mode=False,
        )
        is False
    )

    # 5. Test-mode flag allows local testing without signature
    assert (
        verify_webhook_signature(
            "paystack",
            raw_body,
            {},
            secret=TEST_SECRET,
            test_mode=True,
        )
        is True
    )


def test_webhook_rejects_invalid_hmac_signature_endpoint(client):
    """Verifies POST /webhooks/<provider> rejects invalid HMAC signatures with 401."""
    payload = {
        "event": "charge.success",
        "data": {"id": 901, "reference": "PSK_BAD_SIG_01", "amount": 100000},
    }
    raw_bytes = json.dumps(payload).encode("utf-8")

    resp = client.post(
        "/webhooks/paystack",
        data=raw_bytes,
        headers={
            "Content-Type": "application/json",
            "x-paystack-signature": "tampered_hmac_digest",
        },
    )
    assert resp.status_code == 401
    body = resp.get_json()
    assert body["status"] == "failed"
    assert body["error"] == "INVALID_SIGNATURE"
    assert body["event"]["signature_valid"] is False


def test_duplicate_event_idempotency_blocking(client):
    """Verifies duplicate webhook events with the same event_id are blocked idempotently."""
    payload = {
        "event": "charge.success",
        "data": {
            "id": 100200,
            "reference": "PSK_IDEMP_001",
            "amount": 500000,
            "currency": "NGN",
            "customer": {"email": "adaeze@fintech.ng"},
        },
    }
    raw_bytes = json.dumps(payload).encode("utf-8")
    sig = compute_hmac_sha512(raw_bytes, TEST_SECRET)

    # First delivery -> processed and stored in SQLite
    resp1 = client.post(
        "/webhooks/paystack",
        data=raw_bytes,
        headers={
            "Content-Type": "application/json",
            "x-paystack-signature": sig,
        },
    )
    assert resp1.status_code == 200
    data1 = resp1.get_json()
    assert data1["status"] == "processed"
    assert data1["duplicate"] is False
    assert data1["event"]["event_id"] == "paystack_PSK_IDEMP_001"

    # Second identical delivery -> blocked by idempotency filter
    resp2 = client.post(
        "/webhooks/paystack",
        data=raw_bytes,
        headers={
            "Content-Type": "application/json",
            "x-paystack-signature": sig,
        },
    )
    assert resp2.status_code == 200
    data2 = resp2.get_json()
    assert data2["status"] == "duplicate_ignored"
    assert data2["duplicate"] is True
    assert data2["duplicates_blocked"] == 1
    assert data2["event"]["id"] == data1["event"]["id"]

    # Ensure only 1 row was persisted in SQLite
    events_resp = client.get("/api/events").get_json()
    assert events_resp["total"] == 1
    assert events_resp["metrics"]["duplicates_blocked"] == 1


def test_get_events_filtering_and_replay_increments_retry_count(client):
    # Create 1 processed Paystack event via simulate
    client.post(
        "/api/simulate",
        json={"provider": "paystack", "amount": 10000, "currency": "NGN"},
    )
    # Create 1 failed Flutterwave event via simulate_failure
    sim_fail = client.post(
        "/api/simulate",
        json={
            "provider": "flutterwave",
            "amount": 5000,
            "currency": "KES",
            "simulate_failure": True,
        },
    ).get_json()

    event_pk = sim_fail["event"]["id"]
    assert sim_fail["event"]["status"] == "failed"
    assert sim_fail["event"]["retry_count"] == 0

    failed_only = client.get("/api/events?status=failed").get_json()
    assert failed_only["total"] == 1
    assert failed_only["events"][0]["provider"] == "flutterwave"

    # Replay the failed event -> status becomes 'processed' and retry_count increments to 1
    replay_resp = client.post(f"/api/events/{event_pk}/replay")
    assert replay_resp.status_code == 200
    replay_data = replay_resp.get_json()
    assert replay_data["status"] == "processed"
    assert replay_data["retry_count"] == 1
