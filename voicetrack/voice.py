import logging
import os
import threading

VOICE_AVAILABLE = False

try:
    import vosk
    import sounddevice
    VOICE_AVAILABLE = True
except ImportError:
    pass


class VoiceRecorder:
    def __init__(self, model_path: str, callback):
        self._model_path = model_path
        self._callback = callback
        self._thread = None
        self._running = False
        self._model = None

        if VOICE_AVAILABLE and os.path.exists(model_path):
            try:
                self._model = vosk.Model(model_path)
            except Exception as e:
                logging.warning("Vosk model load failed: %s", e)
        elif VOICE_AVAILABLE:
            logging.warning("Vosk model path not found: %s", model_path)

    def start(self) -> None:
        if not self.is_available() or self._model is None:
            return
        self._running = True
        self._thread = threading.Thread(target=self._record, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def is_available(self) -> bool:
        return VOICE_AVAILABLE

    def _record(self):
        rec = vosk.KaldiRecognizer(self._model, 16000)
        with sounddevice.RawInputStream(
            samplerate=16000, blocksize=8000, dtype="int16", channels=1
        ) as stream:
            while self._running:
                data, _ = stream.read(8000)
                if rec.AcceptWaveform(bytes(data)):
                    import json
                    result = json.loads(rec.Result())
                    text = result.get("text", "").strip()
                    if text:
                        self._callback(text)
