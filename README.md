### Backend overview

The backend is built with **FastAPI**, designed to be lightweight but production-ready.  
It includes:
- **POST /transaction** → to create and route a transaction via the smart router.  
- **Smart routing logic** in `smart_router.py` → selects the best PSP (Stripe, Adyen, Rapyd, Wise…) based on country, currency, fees, and device.  
- **Authentication** via API key for secure access.  
- **/metrics endpoint** → provides live stats and transaction volumes.  
- **SQLite database** → stores all transactions for persistence and analysis.  

The architecture is modular and easy to extend, making it simple to plug in new PSPs or add analytics features.
