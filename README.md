# VoiceTrack

VoiceTrack is a fully offline desktop expense tracker. You can type or speak plain English like `spent 1500 on electricity bill today`, and a local Ollama model extracts the transaction fields. Python handles the database, totals, filtering, charts, and exports.

## What You Need

- Windows 10 or 11
- Python 3.10 or newer.
- Ollama installed
- One local model, recommended:

```powershell
ollama pull mistral:7b-instruct-q4_K_M
```

You can also use your `llama3.2:3b` model by changing `.env`.

For slower laptops, this is the recommended `.env`:

```text
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.2:3b
OLLAMA_FALLBACK_MODEL=mistral:7b-instruct-q4_K_M
OLLAMA_TIMEOUT_SECONDS=20
```

## Setup

1. Open PowerShell in this folder.
2. Create a virtual environment:

```powershell
python -m venv .venv
```

You do not need to activate it. Calling the virtualenv Python directly avoids PowerShell execution-policy problems.

3. Install the core desktop app dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

4. Voice support:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-voice.txt
```

VoiceTrack uses the offline Vosk model from `models\vosk-model-small-en-us-0.15`.

5. Optional: copy `.env.example` to `.env` and edit model names or database path.

6. Start Ollama in the background, then run:

```powershell
.\.venv\Scripts\python.exe main.py
```

## Offline Notes

- Ollama runs locally on your laptop.
- SQLite stores data in one local file.
- Charts are drawn locally with matplotlib.
- Voice input uses `sounddevice` for microphone audio and `Vosk` for offline speech recognition.
- No paid API, cloud database, or internet service is used after dependencies and models are installed.

## Database Location

By default, VoiceTrack creates:

```text
C:\Users\<you>\VoiceTrack\data.db
```

Set `VOICETRACK_DB_PATH` in `.env` if you want a different path.

## How It Works

1. You type or speak a transaction.
2. Ollama returns JSON with type, amount, category, description, date, time, and confidence.
3. VoiceTrack shows an editable preview.
4. You confirm the preview.
5. Python writes the row to SQLite.
6. Python recalculates totals and redraws the dashboard.

If Ollama is still loading or times out, VoiceTrack uses a low-confidence local fallback for simple entries and asks you to review the fields before saving.

## UI

- Use the switch in the top-right corner to toggle light and dark mode.
- Add Entry runs the AI extraction in the background, so the app stays responsive while Ollama thinks.
- The `Microphone` button works like a toggle: click `Microphone` to start listening, speak, then click `Stop`. Recognized speech is added to the text box, then you click `Process`.
- If the app says microphone packages are missing, install `requirements-voice.txt`.

## Tests

Run the test suite with:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The tests use a fake extractor, so they do not need Ollama or a microphone.

## Build an EXE

Install PyInstaller if you want a Windows executable:

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\pyinstaller.exe --onefile --windowed main.py --name VoiceTrack
```

The app will appear in the `dist` folder.
