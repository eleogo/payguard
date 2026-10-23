from abc import ABC, abstractmethod
import hashlib
import hmac
from typing import Any, Dict, Mapping, Optional
from models import NormalizedWebhookMeta


class WebhookVerifier(ABC):
    """Base interface for African payment gateway webhook signature verifiers."""

    provider_name: str = ""
    signature_header: str = ""

    @staticmethod
    def get_header(headers: Mapping[str, str], target_name: str) -> Optional[str]:
        target_lower = target_name.lower()
        for key, value in headers.items():
            if key.lower() == target_lower:
                return value
        return None

    @abstractmethod
    def verify(self, raw_body: bytes, headers: Mapping[str, str], secret: str) -> bool:
        """Cryptographically verify the webhook signature in constant time."""
        pass

    @abstractmethod
    def extract_metadata(self, payload: Dict[str, Any]) -> NormalizedWebhookMeta:
        """Extract normalized payment telemetry from provider-specific payload."""
        pass


class PaystackVerifier(WebhookVerifier):
    """
    Paystack Webhook Verifier.
    Spec: HMAC-SHA512 over raw request body using merchant secret key,
    sent as hex digest in `x-paystack-signature`.
    Note: Paystack reports amounts in minor units (kobo/pesewas/cents).
    """

    provider_name = "paystack"
    signature_header = "x-paystack-signature"

    def verify(self, raw_body: bytes, headers: Mapping[str, str], secret: str) -> bool:
        signature = self.get_header(headers, self.signature_header)
        if not signature or not secret:
            return False
        computed = hmac.new(
            secret.encode("utf-8"),
            msg=raw_body,
            digestmod=hashlib.sha512,
        ).hexdigest()
        return hmac.compare_digest(computed.lower(), signature.strip().lower())

    def extract_metadata(self, payload: Dict[str, Any]) -> NormalizedWebhookMeta:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}

        raw_amount = data.get("amount", 0)
        try:
            # Paystack amounts are in kobo/pesewas/cents (divide by 100 for major unit)
            amount_major = round(float(raw_amount) / 100.0, 2)
        except (TypeError, ValueError):
            amount_major = 0.0

        ext_id = data.get("id") or payload.get("id")
        ref = data.get("reference") or data.get("transfer_code")

        return NormalizedWebhookMeta(
            provider=self.provider_name,
            event_type=str(payload.get("event") or "charge.unknown"),
            external_event_id=str(ext_id) if ext_id is not None else None,
            reference=str(ref) if ref is not None else None,
            amount=amount_major,
            currency=str(data.get("currency") or "NGN"),
            customer_email=customer.get("email") or data.get("email"),
        )


class FlutterwaveVerifier(WebhookVerifier):
    """
    Flutterwave Webhook Verifier.
    Spec: Sends secret hash in `verif-hash` header (v3 standard) OR HMAC-SHA256
    in `flutterwave-signature`. Uses constant-time digest comparison.
    Note: Flutterwave reports amounts in major currency units.
    """

    provider_name = "flutterwave"
    signature_header = "verif-hash"

    def verify(self, raw_body: bytes, headers: Mapping[str, str], secret: str) -> bool:
        if not secret:
            return False

        verif_hash = self.get_header(headers, "verif-hash")
        if verif_hash:
            return hmac.compare_digest(
                verif_hash.strip().encode("utf-8"),
                secret.strip().encode("utf-8"),
            )

        hmac_sig = self.get_header(headers, "flutterwave-signature")
        if hmac_sig:
            computed = hmac.new(
                secret.encode("utf-8"),
                msg=raw_body,
                digestmod=hashlib.sha256,
            ).hexdigest()
            return hmac.compare_digest(computed.lower(), hmac_sig.strip().lower())

        return False

    def extract_metadata(self, payload: Dict[str, Any]) -> NormalizedWebhookMeta:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}

        raw_amount = data.get("amount", payload.get("amount", 0))
        try:
            amount_major = round(float(raw_amount), 2)
        except (TypeError, ValueError):
            amount_major = 0.0

        ext_id = data.get("id") or payload.get("id")
        ref = data.get("tx_ref") or data.get("flw_ref") or payload.get("txRef")

        event_type = (
            payload.get("event")
            or payload.get("event.type")
            or "charge.completed"
        )

        return NormalizedWebhookMeta(
            provider=self.provider_name,
            event_type=str(event_type),
            external_event_id=str(ext_id) if ext_id is not None else None,
            reference=str(ref) if ref is not None else None,
            amount=amount_major,
            currency=str(data.get("currency") or payload.get("currency") or "NGN"),
            customer_email=customer.get("email"),
        )


VERIFIER_REGISTRY: Dict[str, WebhookVerifier] = {}


def register_verifier(verifier: WebhookVerifier) -> None:
    VERIFIER_REGISTRY[verifier.provider_name.lower()] = verifier


def get_verifier(provider: str) -> Optional[WebhookVerifier]:
    return VERIFIER_REGISTRY.get(provider.lower().strip())


# Register built-in African fintech payment providers
register_verifier(PaystackVerifier())
register_verifier(FlutterwaveVerifier())
