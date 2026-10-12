"""Run inside the deployed backend; GET-only, no database or raw provider output."""
import argparse
import json

from app.core.config import Settings
from app.modules.payments.mercado_pago import MercadoPagoPaymentProvider


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-application-id", required=True)
    args = parser.parse_args()
    try:
        settings = Settings()
        provider = MercadoPagoPaymentProvider(settings)
        event = provider.diagnose_payment_environment(args.expected_application_id)
    except Exception:
        # No exception text: settings/provider errors can contain sensitive values.
        event = {"event": "mercado_pago_environment_diagnostic", "result": "configuration_or_argument_invalid"}
    print(json.dumps(event, ensure_ascii=True))


if __name__ == "__main__":
    main()
