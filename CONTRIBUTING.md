# Contributing to PayGuard

Thank you for your interest in contributing to **PayGuard**, the open-source payment webhook gateway and dead-letter queue built for African fintechs!

Across Africa's fragmented payment landscape (Paystack, Flutterwave, Monnify, Korapay, Squad, M-Pesa, Pawapay, etc.), every payment gateway implements webhook signing, headers, and payload envelopes differently. Adding a new payment provider signature verifier to PayGuard is one of the highest-impact contributions you can make.

---

## Architecture of Signature Verifiers

All signature verification logic lives in [`server/verifiers.py`](server/verifiers.py). Each provider implements the `WebhookVerifier` base interface:

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, Mapping, Optional
from models import NormalizedWebhookMeta

class WebhookVerifier(ABC):
    provider_name: str
    signature_header: str

    @abstractmethod
    def verify(self, raw_body: bytes, headers: Mapping[str, str], secret: str) -> bool:
        """Cryptographically verify the webhook signature using constant-time comparison."""
        pass

    @abstractmethod
    def extract_metadata(self, payload: Dict[str, Any]) -> NormalizedWebhookMeta:
        """Extract normalized fields (event_type, external_event_id, reference, amount, currency, customer_email)."""
        pass
```

---

## Step-by-Step Guide: Adding a New Payment Provider Verifier

### Step 1: Understand the Provider's Signing Specification

Before writing code, check the payment provider's official documentation for:
1. **Header Name**: Which HTTP header carries the signature? (e.g., `x-paystack-signature`, `verif-hash`, `monnify-signature`, `x-korapay-signature`).
2. **Algorithm**: Is it `HMAC-SHA512` over the raw request body, `HMAC-SHA256` over a subset of fields (like `data`), or a static secret hash?
3. **Encoding**: Is the digest hex-encoded (`hexdigest()`) or base64-encoded?
4. **Amount Format**: Does the provider send amounts in minor units (e.g., kobo/pesewas/cents like Paystack) or major units (like Flutterwave)?

### Step 2: Implement the Verifier Class in `server/verifiers.py`

Create a new subclass of `WebhookVerifier`. **Security Requirement:** Always use `hmac.compare_digest()` to prevent timing side-channel attacks. Never use `==` to compare cryptographic signatures or hashes.

#### Example: Adding Korapay (`x-korapay-signature`)

Korapay signs only the JSON-serialized `data` object using `HMAC-SHA256` with the merchant's secret key, or signs the payload depending on the version. Here is how to implement a provider verifier:

```python
import hashlib
import hmac
from typing import Any, Dict, Mapping
from models import NormalizedWebhookMeta
from verifiers import WebhookVerifier, register_verifier


class MonnifyVerifier(WebhookVerifier):
    provider_name = "monnify"
    signature_header = "monnify-signature"

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
        event_data = payload.get("eventData", {}) if isinstance(payload.get("eventData"), dict) else {}
        customer = event_data.get("customer", {}) if isinstance(event_data.get("customer"), dict) else {}
        return NormalizedWebhookMeta(
            provider=self.provider_name,
            event_type=str(payload.get("eventType") or "UNKNOWN_EVENT"),
            external_event_id=str(
                event_data.get("transactionReference")
                or event_data.get("paymentReference")
                or ""
            ) or None,
            reference=str(event_data.get("paymentReference") or "") or None,
            amount=float(event_data.get("amountPaid") or 0.0),
            currency=str(event_data.get("currency") or "NGN"),
            customer_email=customer.get("email"),
        )
```

### Step 3: Register the Verifier & Secret Key

1. Register your verifier in the `VERIFIER_REGISTRY` in `server/verifiers.py`:
   ```python
   register_verifier(MonnifyVerifier())
   ```
2. Add the default secret environment variable in `server/config.py` (e.g., `MONNIFY_SECRET_KEY`).

### Step 4: Add Unit Tests in `server/tests/test_payguard.py`

Every new payment provider verifier **must** include at least 3 tests:
1. **Valid signature test**: Verifies that a properly signed payload returns `200 OK` / `202 Accepted` and stores `signature_valid=True`.
2. **Invalid signature test**: Verifies that a tampered payload or wrong secret returns `401 Unauthorized` and logs the rejected event for security auditing.
3. **Metadata extraction & idempotency test**: Verifies that `reference`, `amount`, `currency`, and `external_event_id` are normalized properly and duplicate webhook deliveries are deduplicated.

Run the test suite before submitting your Pull Request:

```bash
cd server
pytest -v
```

---

## Code Style & Pull Request Checklist

- [ ] Uses `hmac.compare_digest` for all signature comparisons.
- [ ] Operates on the **raw request bytes** (`request.get_data()`) rather than re-serialized JSON (`json.dumps()`), since whitespace or key ordering differences break HMAC verification.
- [ ] Normalizes minor currency units (e.g., kobo -> NGN) consistently in `extract_metadata()`.
- [ ] Includes unit tests in `server/tests/test_payguard.py`.
- [ ] Updates `README.md` with sample `curl` commands for testing the new provider locally.
