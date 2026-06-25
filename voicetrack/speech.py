"""Microphone speech-to-text using the offline app pipeline."""

from __future__ import annotations

import json
import queue
from threading import Event
from typing import Callable
import importlib.util
from pathlib import Path


TextCallback = Callable[[str], None]
StatusCallback = Callable[[str], None]


def missing_voice_dependencies() -> list[str]:
    """Return optional packages that are needed for offline microphone input."""
    packages = {
        "SpeechRecognition": "speech_recognition",
        "sounddevice": "sounddevice",
        "vosk": "vosk",
    }
    return [name for name, module in packages.items() if importlib.util.find_spec(module) is None]


def listen_once(timeout: int = 5, phrase_time_limit: int = 8) -> str:
    """Capture one phrase from the microphone and return recognized text."""
    import speech_recognition as sr

    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        recognizer.adjust_for_ambient_noise(source, duration=0.5)
        audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)

    # Sphinx keeps voice recognition offline. If it is missing, users can still type.
    try:
        return recognizer.recognize_sphinx(audio)
    except Exception as exc:
        raise RuntimeError(
            "Offline speech recognition is not available. Install pocketsphinx or use text input."
        ) from exc


def listen_until_stopped(
    stop_event: Event,
    on_text: TextCallback,
    on_status: StatusCallback | None = None,
    model_path: str | Path = "models/vosk-model-small-en-us-0.15",
    phrase_time_limit: int = 5,
) -> None:
    """Listen in short chunks until the UI asks the microphone to stop."""
    missing = missing_voice_dependencies()
    if missing:
        raise RuntimeError("Install voice packages first: " + ", ".join(missing))

    try:
        import sounddevice as sd
        from vosk import KaldiRecognizer, Model
    except Exception as exc:
        raise RuntimeError("Install requirements-voice.txt to enable microphone input.") from exc

    model_dir = Path(model_path)
    if not model_dir.exists():
        raise RuntimeError(f"Vosk model not found at {model_dir}. Download the model first.")

    audio_queue: queue.Queue[bytes] = queue.Queue()
    sample_rate = 16000
    recognizer = KaldiRecognizer(Model(str(model_dir)), sample_rate)

    def callback(indata, frames, time_info, status):
        if status and on_status:
            on_status(str(status))
        audio_queue.put(bytes(indata))

    if on_status:
        on_status("Listening... press Stop when done.")

    with sd.RawInputStream(
        samplerate=sample_rate,
        blocksize=8000,
        dtype="int16",
        channels=1,
        callback=callback,
    ):
        empty_reads = 0
        while not stop_event.is_set():
            try:
                data = audio_queue.get(timeout=0.25)
            except queue.Empty:
                empty_reads += 1
                if empty_reads >= phrase_time_limit * 4 and on_status:
                    on_status("Still listening... speak clearly, then press Stop.")
                    empty_reads = 0
                continue
            empty_reads = 0
            if recognizer.AcceptWaveform(data):
                text = json.loads(recognizer.Result()).get("text", "").strip()
                if text:
                    on_text(text)
                    if on_status:
                        on_status(f'Captured: "{text}"')

    final_text = json.loads(recognizer.FinalResult()).get("text", "").strip()
    if final_text:
        on_text(final_text)
