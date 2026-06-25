# VoiceTrack Concepts

## SQLite

SQLite is a small database stored in a single file. VoiceTrack uses it for `transactions` and `categories`, so there is no server to install or run.

## Ollama

Ollama runs language models on your computer. VoiceTrack sends your sentence to Ollama and asks for JSON only. The model extracts meaning, but it does not calculate totals.

## Voice Recognition

VoiceTrack captures microphone audio with `sounddevice` and converts it to text with Vosk. Vosk runs locally using a downloaded speech model, so voice input works offline after setup.

## CustomTkinter

CustomTkinter is a modern wrapper around Tkinter. It creates the desktop windows, buttons, fields, navigation, and dark theme.

## Matplotlib

Matplotlib draws the dashboard and report charts. VoiceTrack embeds those charts inside CustomTkinter panels.

## JSON

JSON is a structured text format. Ollama returns fields like `amount`, `category`, and `date` in JSON so Python can validate and save them safely.

## How They Connect

The app flow is:

```text
voice or text -> Ollama JSON -> Python validation -> SQLite row -> Python totals -> CustomTkinter UI + matplotlib charts
```

The important boundary is simple: Ollama understands language, Python handles all arithmetic and storage.
