# VoiceTrack Presentation Cheat Sheet

Use this when someone asks how the project works.

## One-line explanation

VoiceTrack is a fully offline desktop expense tracker. The user types or speaks a normal sentence, a local Ollama model extracts transaction fields, Python validates and saves the data in SQLite, and the dashboard/reports are calculated from the database.

## Why these models were used

- The app must run offline on an i5 6th Gen laptop with 8GB RAM.
- Large cloud models were not used because the requirement says no paid APIs and no cloud dependency.
- `llama3.2:3b` is used first because it is smaller and faster on CPU.
- `mistral:7b-instruct-q4_K_M` is available as a stronger fallback, but it is slower.
- Q4 quantized models are used because they fit better in RAM than full precision models.

If asked why not other models:

> We selected models that balance offline use, RAM limits, speed, and extraction quality. Bigger models may understand better, but they would be too slow or too heavy for the target laptop.

## What happens when the user enters text

Example:

```text
spent 3000 on electricity bill today
```

Flow:

1. `ui.py` reads the text from the Add Entry screen.
2. `pipeline.py` sends the text for extraction.
3. `extractors.py` asks Ollama to return JSON.
4. If Ollama is slow or unavailable, `extractors.py` uses a local fallback parser.
5. `pipeline.py` validates amount, type, category, date, and time.
6. The UI shows an editable preview.
7. When the user clicks Confirm, `db.py` writes the transaction into SQLite.
8. Dashboard and reports read from SQLite and recalculate totals using Python.

## What low-confidence fallback means

Low confidence does not mean the app is broken.

It means Ollama did not return quickly enough or did not return usable JSON, so the app used the local fallback parser. The fallback is safer than failing completely, but it asks the user to review before saving.

## Static and dynamic prompting

Static prompt:

- The fixed instruction sent to Ollama.
- It says: return only JSON, choose type, amount, category, description, date, time, and confidence.

Dynamic prompt:

- The app adds the current date, current time, and the user's sentence.
- This lets the model understand words like today or yesterday.

Example:

```text
Static: Extract JSON only.
Dynamic: Current date is 2026-06-29. User input is "spent 500 on cab today".
```

## Why Python does the math

The LLM is not trusted for arithmetic. Python calculates:

- total income
- total expenses
- net balance
- daily/weekly/monthly filters
- category totals
- chart data

This is more reliable and easier to test.

## Where the data goes

Transactions are saved in this SQLite file:

```text
C:\Users\ammar\VoiceTrack\data.db
```

Each row has:

- type: income or expense
- amount
- category
- description
- transaction date
- transaction time
- created_at, meaning when the entry was added

## What happens next month

Old data does not disappear.

If June ends and July starts:

- June transactions stay in SQLite with June dates.
- July transactions are added with July dates.
- Dashboard filters use SQL date ranges to show current month, previous month, week, today, or all time.
- Reports can compare income and expenses month by month.

So the database is the memory of the app, not the LLM context window.

## What if the LLM context window fills up

This app does not send the whole database history to the LLM.

For each entry, the app sends only:

- the extraction instruction
- current date/time
- the one sentence the user entered

Saved financial history is stored in SQLite, not inside the LLM conversation. So old transactions do not depend on the model context.

## How this sentence is handled

Input:

```text
i went to texas fries on cab for 500
```

Meaning:

- Texas Fries is the destination.
- Cab is the service paid for.
- 500 belongs to transport, not food.

Expected extraction:

```json
{
  "type": "expense",
  "amount": 500,
  "category": "Transport",
  "description": "cab to texas fries",
  "date": "today",
  "time": null,
  "confidence": "low"
}
```

If Ollama works, it may be high confidence. If fallback is used, the app marks it low confidence and asks the user to review.

## What if the user enters multiple transactions in one prompt

Example:

```text
spent 500 on cab and paid 1200 for dinner
```

Current safe behavior:

> Multiple transactions detected. Please enter one transaction at a time.

Reason:

- The current database save flow saves one transaction per confirmation.
- Splitting one sentence into many rows needs a separate multi-transaction review UI.
- Guess-saving multiple rows would be risky for money data.

Production improvement:

- Add a multi-transaction preview table.
- Show each detected transaction as a separate editable row.
- User confirms all rows together.

## File interaction map

```text
main.py
  -> starts run_app()

voicetrack/ui.py
  -> screens, buttons, charts, preview form

voicetrack/pipeline.py
  -> validation and save workflow

voicetrack/extractors.py
  -> Ollama JSON extraction and fallback parser

voicetrack/dates.py
  -> today, yesterday, last week, last month, weekdays

voicetrack/db.py
  -> SQLite tables, inserts, reads, totals, filters, CSV export

voicetrack/speech.py
  -> microphone audio and offline Vosk speech-to-text

voicetrack/config.py
  -> .env settings

voicetrack/constants.py
  -> categories and UI theme colors
```

## Honest limitation statement

Say this if challenged:

> The current version is a single-transaction offline tracker. It is designed to be safe: if extraction is uncertain, it asks for review instead of silently saving. Multi-transaction extraction can be added, but it needs a separate preview table to avoid wrong financial records.

