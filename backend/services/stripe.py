import os, stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

def create_and_confirm_intent(amount: float, currency: str) -> dict:
    """
    Démo réaliste : crée + confirme un PaymentIntent avec un PM de test côté serveur.
    """
    amount_in_minor = int(round(amount * 100))  # cents
    intent = stripe.PaymentIntent.create(
        amount=amount_in_minor,
        currency=currency.lower(),
        payment_method="pm_card_visa",          # carte de test Stripe
        confirmation_method="automatic",
        confirm=True,
    )
    status = "success" if intent.status in ("succeeded", "requires_capture") else "failed"
    return {
        "status": status,
        "psp_tx_id": intent.id,                 # ex: pi_123
        "raw": {"status": intent.status},
    }
