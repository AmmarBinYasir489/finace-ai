import os
from dotenv import load_dotenv

load_dotenv()

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")
OLLAMA_FALLBACK_MODEL = os.getenv("OLLAMA_FALLBACK_MODEL", "")

# Support both names. The app previously read OLLAMA_TIMEOUT only, while the
# project .env used OLLAMA_TIMEOUT_SECONDS, which silently forced a 5s timeout.
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", os.getenv("OLLAMA_TIMEOUT", "45")))

VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH", r"models\vosk-model-small-en-us-0.15")
DB_PATH = os.getenv(
    "VOICETRACK_DB_PATH",
    os.getenv("DB_PATH", os.path.join(os.path.expanduser("~"), "VoiceTrack", "data.db")),
)
