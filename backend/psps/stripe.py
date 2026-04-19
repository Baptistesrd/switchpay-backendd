"""
Stripe PSP client — real Stripe API integration via PaymentIntents.

Requires STRIPE_SECRET_KEY in the environment.
  - Test mode : sk_test_...  (use Stripe test card numbers, no real charges)
  - Live mode : sk_live_...  (real charges — use only in production)

Payment flow
────────────
1. Create a PaymentIntent with amount + currency.
2. If the caller supplies a `payment_method` (pm_... or tok_...) in the
   transaction data, confirm the intent immediately on the server side.
   Without one the intent is left in requires_payment_method state and
   the response status is mapped to "failed" — the frontend must complete
   confirmation using the returned psp_tx_id (the PaymentIntent id).
3. Map Stripe's intent status → SwitchPay status:
     "succeeded"  → "success"
     anything else → "failed"

Amount conversion
─────────────────
Stripe requires amounts in the smallest currency unit (cents for USD/EUR).
`montant` is multiplied by 100 and rounded.  Zero-decimal currencies
(JPY, KRW, …) are forwarded as-is.
"""

import asyncio
import logging
import os
from typing import Optional

import stripe

from backend.psps.base import BasePSPClient

logger = logging.getLogger("switchpay.psp.stripe")

# Zero-decimal currencies — do NOT multiply by 100.
# Source: https://docs.stripe.com/currencies#zero-decimal
_ZERO_DECIMAL_CURRENCIES = {
    "BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW",
    "MGA", "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
}


def _to_stripe_amount(montant: float, devise: str) -> int:
    """Convert a decimal amount to Stripe's smallest-unit integer."""
    if devise.upper() in _ZERO_DECIMAL_CURRENCIES:
        return int(montant)
    return round(montant * 100)


class StripeClient(BasePSPClient):
    """Real Stripe PaymentIntent client.

    Instantiation reads STRIPE_SECRET_KEY from the environment and raises
    RuntimeError immediately if it is absent, so misconfiguration is caught
    at startup rather than on the first live transaction.
    """

    name = "stripe"

    def __init__(self) -> None:
        key = os.getenv("STRIPE_SECRET_KEY", "").strip()
        if not key:
            raise RuntimeError(
                "STRIPE_SECRET_KEY is not set. "
                "Add sk_test_... (test) or sk_live_... (live) to your environment."
            )
        # StripeClient is the modern service-based API introduced in stripe-python 12.
        # It avoids mutating the global stripe.api_key and is safe for multi-tenant use.
        self._client = stripe.StripeClient(key)

    async def process_payment(self, data: dict) -> dict:
        """Create (and optionally confirm) a Stripe PaymentIntent.

        Args:
            data: Transaction dict containing at minimum:
                    montant  (float) — transaction amount
                    devise   (str)   — ISO 4217 currency code (e.g. "USD")
                    pays     (str)   — ISO 3166-1 alpha-2 country code
                    id       (str)   — SwitchPay transaction ID (stored in metadata)
                  Optionally:
                    payment_method (str) — pm_... or tok_... from Stripe.js;
                                           when present the intent is confirmed server-side.

        Returns:
            Dict with at minimum {"status": "success"|"failed", "psp_tx_id": str|None}.
            Extra keys (stripe_status, amount, currency, error, error_code) are included
            for observability and are stored in raw_response by the transaction router.
        """
        montant: float = data.get("montant", 0)
        devise: str = data.get("devise", "usd")
        tx_id: str = data.get("id", "")
        payment_method: Optional[str] = data.get("payment_method")

        amount = _to_stripe_amount(montant, devise)

        params: dict = {
            "amount": amount,
            "currency": devise.lower(),
            "metadata": {
                "switchpay_tx_id": tx_id,
                "pays": data.get("pays", ""),
                "device": data.get("device", ""),
            },
            # Allow any payment method type; disable redirect-based methods
            # so server-side confirmation works without a return_url.
            "automatic_payment_methods": {
                "enabled": True,
                "allow_redirects": "never",
            },
        }

        if payment_method:
            params["payment_method"] = payment_method
            params["confirm"] = True

        try:
            intent = await asyncio.to_thread(
                self._client.payment_intents.create, params
            )

        except stripe.CardError as exc:
            # The card was declined. exc.error contains structured details.
            err = exc.error
            logger.warning(
                "Stripe card declined | code=%s decline_code=%s tx_id=%s",
                err.code,
                getattr(err, "decline_code", None),
                tx_id,
            )
            return {
                "status": "failed",
                "psp_tx_id": err.payment_intent.id if getattr(err, "payment_intent", None) else None,
                "error": err.message,
                "error_code": err.code,
            }

        except stripe.RateLimitError as exc:
            logger.error("Stripe rate-limit exceeded | tx_id=%s: %s", tx_id, exc)
            return {
                "status": "failed",
                "psp_tx_id": None,
                "error": "Stripe rate limit exceeded — retry after a short delay.",
                "error_code": "rate_limit_error",
            }

        except stripe.InvalidRequestError as exc:
            logger.error("Stripe invalid request | tx_id=%s: %s", tx_id, exc)
            return {
                "status": "failed",
                "psp_tx_id": None,
                "error": str(exc),
                "error_code": "invalid_request_error",
            }

        except stripe.AuthenticationError as exc:
            # Bad API key — this should never reach production if startup checks pass.
            logger.critical("Stripe authentication failed | tx_id=%s: %s", tx_id, exc)
            return {
                "status": "failed",
                "psp_tx_id": None,
                "error": "Stripe authentication failed — check STRIPE_SECRET_KEY.",
                "error_code": "authentication_error",
            }

        except stripe.APIConnectionError as exc:
            logger.error("Stripe network error | tx_id=%s: %s", tx_id, exc)
            return {
                "status": "failed",
                "psp_tx_id": None,
                "error": "Network error reaching Stripe — will retry.",
                "error_code": "api_connection_error",
            }

        except stripe.StripeError as exc:
            # Catch-all for any other Stripe error (e.g. APIError 5xx).
            logger.error("Stripe API error | tx_id=%s: %s", tx_id, exc)
            return {
                "status": "failed",
                "psp_tx_id": None,
                "error": str(exc),
                "error_code": "stripe_error",
            }

        # Map Stripe's intent status to SwitchPay's binary status.
        # "succeeded"            → payment captured, all done.
        # "requires_confirmation" etc. → frontend must complete the flow.
        status = "success" if intent.status == "succeeded" else "failed"

        return {
            "status": status,
            "psp_tx_id": intent.id,
            "stripe_status": intent.status,
            "amount": intent.amount,
            "currency": intent.currency,
        }
