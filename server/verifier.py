import hashlib
import hmac
import os
from typing import Mapping, Optional


DEFAULT_WEBHOOK_SECRET = os.getenv(
    "WEBHOOK_SECRET",
    os.getenv("PAYSTACK_SECRET_KEY", "sk_test_payguard_secret_key"),
)
DEFAULT_FLUTTERWAVE_SECRET = os.getenv(
    "FLUTTERWAVE_SECRET_HASH",
    os.getenv("WEBHOOK_SECRET", "sk_test_payguard_secret_key"),
)


def _get_header_case_insensitive(
    headers: Mapping[str, str], target_header: str
) -> Optional[str]:
    target = target_header.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def compute_hmac_sha512(raw_body: bytes, secret: str) -> str:
    """Compute hex-encoded HMAC-SHA512 digest over raw request bytes."""
    return hmac.new(
        secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha512,
    ).hexdigest()


def verify_webhook_signature(
    provider: str,
    raw_body: bytes,
    headers: Mapping[str, str],
    secret: Optional[str] = None,
    test_mode: Optional[bool] = None,
) -> bool:
    """
    Verifies HMAC-SHA512 webhook signatures for Paystack (`x-paystack-signature`)
    and Flutterwave (`verif-hash`) using a configurable webhook secret.

    Includes a `test_mode` flag (via argument, `TEST_MODE=true` env var, or
    `X-PayGuard-Test-Mode: true` request header) so developers can test locally
    without computing signatures manually.
    """
    provider_clean = (provider or "").strip().lower()
    if provider_clean not in ("paystack", "flutterwave"):
        raise ValueError(f"Unsupported payment provider: {provider}")

    # Check if local developer test-mode is enabled
    if test_mode is None:
        env_test_mode = os.getenv("TEST_MODE", "false").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        header_test_mode = (
            _get_header_case_insensitive(headers, "x-payguard-test-mode") or ""
        ).strip().lower() in ("1", "true", "yes")
        test_mode = env_test_mode or header_test_mode

    if test_mode:
        return True

    if provider_clean == "paystack":
        effective_secret = secret or os.getenv(
            "PAYSTACK_SECRET_KEY", DEFAULT_WEBHOOK_SECRET
        )
        signature = _get_header_case_insensitive(headers, "x-paystack-signature")
        if not signature or not effective_secret:
            return False
        expected_hmac = compute_hmac_sha512(raw_body, effective_secret)
        return hmac.compare_digest(
            expected_hmac.lower(), signature.strip().lower()
        )

    if provider_clean == "flutterwave":
        effective_secret = secret or os.getenv(
            "FLUTTERWAVE_SECRET_HASH", DEFAULT_FLUTTERWAVE_SECRET
        )
        verif_hash = _get_header_case_insensitive(headers, "verif-hash")
        if not verif_hash or not effective_secret:
            return False

        cleaned_header = verif_hash.strip()
        expected_hmac = compute_hmac_sha512(raw_body, effective_secret)

        # Support both HMAC-SHA512 digest in `verif-hash` and direct secret hash match
        is_hmac_match = hmac.compare_digest(
            expected_hmac.lower(), cleaned_header.lower()
        )
        is_secret_match = hmac.compare_digest(
            effective_secret.strip().encode("utf-8"),
            cleaned_header.encode("utf-8"),
        )
        return is_hmac_match or is_secret_match

    return False
