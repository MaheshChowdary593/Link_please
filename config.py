import os
from dotenv import load_dotenv

load_dotenv()

def get_api_key():
    return os.getenv("API_KEY", "")

def get_verify_signature():
    return os.getenv("VERIFY_SIGNATURE", "true").lower() in ("true", "1", "yes")

API_KEY = get_api_key()
MOCK_API_BASE_URL = os.getenv("MOCK_API_BASE_URL", "https://pseudogram-api.onrender.com").rstrip("/")
DB_PATH = os.getenv("DB_PATH", "app.db")
PORT = int(os.getenv("PORT", "8000"))
VERIFY_SIGNATURE = get_verify_signature()
