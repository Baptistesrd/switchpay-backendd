smart router · PY
Copy

from backend.db.db_utils import get_all_transactions

MIN_SAMPLE_SIZE = 5      
WEIGHT_SUCCESS  = 0.6    
WEIGHT_LATENCY  = 0.4    
HISTORY_WINDOW  = 200    


def _geo_fallback(country: str) -> str:
    c = country.lower()

    if c in ["us", "ca", "gb", "fr", "de", "es", "it", "au", "jp"]:
        return "stripe"
    if c in ["nl", "se", "no", "dk", "fi", "cn"]:
        return "adyen"
    if c in ["pl", "cz", "hu", "ro", "sg", "hk", "in"]:
        return "wise"
    if c in ["br", "ar", "mx", "co", "cl", "za", "ke", "ng"]:
        return "rapyd"

    return "stripe"


def _compute_scores(recent_txs: list) -> dict:
    buckets = {}
    for tx in recent_txs:
        psp = tx.get("psp")
        if not psp:
            continue
        if psp not in buckets:
            buckets[psp] = {"total": 0, "success": 0, "latencies": []}

        buckets[psp]["total"] += 1
        if tx.get("status") == "success":
            buckets[psp]["success"] += 1
        lat = tx.get("latency_ms")
        if lat is not None:
            try:
                buckets[psp]["latencies"].append(float(lat))
            except (TypeError, ValueError):
                pass

    qualified = {
        psp: data
        for psp, data in buckets.items()
        if data["total"] >= MIN_SAMPLE_SIZE
    }
    if not qualified:
        return {}

    success_rates = {
        psp: data["success"] / data["total"]
        for psp, data in qualified.items()
    }
    avg_latencies = {
        psp: (sum(data["latencies"]) / len(data["latencies"]) if data["latencies"] else 300.0)
        for psp, data in qualified.items()
    }

    lat_values = list(avg_latencies.values())
    lat_min = min(lat_values)
    lat_max = max(lat_values)
    lat_range = lat_max - lat_min if lat_max != lat_min else 1.0

    scores = {}
    for psp in qualified:
        success_score = success_rates[psp]
        latency_score = 1.0 - (avg_latencies[psp] - lat_min) / lat_range
        scores[psp] = (WEIGHT_SUCCESS * success_score) + (WEIGHT_LATENCY * latency_score)

    return scores


def smart_router(transaction: dict) -> str:
    country = transaction.get("pays", "")

    try:
        all_txs = get_all_transactions()
        recent_txs = all_txs[:HISTORY_WINDOW]
    except Exception:
        return _geo_fallback(country)

    scores = _compute_scores(recent_txs)

    if not scores:
        return _geo_fallback(country)

    return max(scores, key=lambda psp: scores[psp])
