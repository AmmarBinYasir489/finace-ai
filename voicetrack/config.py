import os
from dotenv import load_dotenv

load_dotenv()

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
OLLAMA_FALLBACK_MODEL = os.getenv("OLLAMA_FALLBACK_MODEL", "mistral:7b-instruct-q4_K_M")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "5"))
VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH", r"models\vosk-model-small-en-us-0.15")
DB_PATH = os.getenv(
    "DB_PATH",
    os.path.join(os.path.expanduser("~"), "VoiceTrack", "data.db"),
)
