def smart_router(transaction: dict) -> str:
    """
    Retourne le meilleur PSP en fonction du pays et devise.
    Semi-réaliste basé sur la spécialisation régionale.
    """
    country = transaction.get("pays", "").lower()
    currency = transaction.get("devise", "").upper()

    # Stripe → US, Canada, UK, Europe Ouest, Australie, Japon
    if country in ["us", "ca", "uk", "fr", "de", "es", "it", "au", "jp"]:
        return "stripe"

    # Adyen → Europe continentale, Chine (via Alipay/WeChat)
    if country in ["nl", "de", "se", "no", "dk", "fi", "cn"]:
        return "adyen"

    # Wise → payout, Asie, Europe de l’Est
    if country in ["pl", "cz", "hu", "ro", "sg", "hk", "in"]:
        return "wise"

    # Rapyd / dLocal → LatAm, Afrique
    if country in ["br", "ar", "mx", "co", "cl", "za", "ke", "ng"]:
        return "rapyd"

    # Par défaut → Stripe
    return "stripe"



