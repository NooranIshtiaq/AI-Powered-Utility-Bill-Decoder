import os
from dotenv import load_dotenv

load_dotenv()

MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "google")
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-3.5-flash-lite")
API_KEY = os.getenv("API_KEY", "")
REQUEST_DELAY_SECONDS = float(os.getenv("REQUEST_DELAY_SECONDS", "4"))  # free tier: 15 req/min
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "4"))
CACHE_DIR = os.getenv("CACHE_DIR", ".cache")
