import os
from dataclasses import dataclass


@dataclass
class Settings:
    database_path: str = os.getenv(
        "DATABASE_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "payguard.db"),
    )
    paystack_secret_key: str = os.getenv(
        "PAYSTACK_SECRET_KEY", "sk_test_payguard_secret_key"
    )
    flutterwave_secret_hash: str = os.getenv(
        "FLUTTERWAVE_SECRET_HASH", "flw_test_payguard_secret_hash"
    )
    target_webhook_url: str = os.getenv(
        "TARGET_WEBHOOK_URL", "http://localhost:5000/api/mock-merchant/success"
    )
    max_retry_attempts: int = int(os.getenv("MAX_RETRY_ATTEMPTS", "3"))
    base_retry_delay_seconds: int = int(os.getenv("BASE_RETRY_DELAY_SECONDS", "5"))
    forward_timeout_seconds: int = int(os.getenv("FORWARD_TIMEOUT_SECONDS", "5"))
    port: int = int(os.getenv("PORT", "5000"))

    def get_secret_for_provider(self, provider: str) -> str:
        provider_key = provider.lower().strip()
        if provider_key == "paystack":
            return self.paystack_secret_key
        if provider_key == "flutterwave":
            return self.flutterwave_secret_hash
        env_var = f"{provider_key.upper()}_SECRET_KEY"
        return os.getenv(env_var, "")


settings = Settings()
