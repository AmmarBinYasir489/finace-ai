# Financial OS — Agent Skill Files

This directory holds one skill file per agent. Each agent loads its behavior ONLY
from its own `<agent_name>.md`. Skill files are the single source of truth for an
agent's role, contract, and constraints.

## Agents

| Skill file        | Agent              | Personality (isolated role)        | LLM? |
|-------------------|--------------------|------------------------------------|------|
| `speech.md`       | Speech Agent       | Language Cleaner (no finance)      | yes  |
| `parser.md`       | Parser Agent       | Event Extractor (no calc)          | yes  |
| `auditor.md`      | Financial Auditor  | Intent Corrector (no calc)         | yes  |
| `memory.md`       | Memory Agent       | Pattern Learner (no decisions)     | yes  |
| `rule_engine.md`  | Rule Engine        | Truth Layer (deterministic only)   | NO   |
| `loan.md`         | Loan Agent         | Debt Tracker (no classification)   | NO   |
| `shared_expense.md`| Shared Expense    | Split Resolver                     | NO   |
| `budget.md`       | Budget Agent       | Budget Analyzer                    | NO   |
| `insight.md`      | Insight Agent      | Reporter (read-only)               | yes  |

## Isolation rules

- An agent may use ONLY its own skill file. It may not borrow instructions from
  another agent or assume meaning beyond its role.
- Each agent is a strictly isolated module with one responsibility.

## Pipeline (strict order — no skipping)

```
User Input
  -> Speech Agent
  -> Parser Agent
  -> Financial Auditor Agent
  -> Memory Agent
  -> VALIDATION GATE
  -> Financial Rule Engine   (ONLY computation)
  -> Loan Agent
  -> Shared Expense Agent
  -> Budget Agent
  -> Database Service
  -> Analytics Service
  -> Insight Agent
  -> Dashboard
```

## Debug envelope (every agent, every step)

```json
{
  "stage": "<agent_name>",
  "input": "<input_snapshot>",
  "output": "<output_snapshot>",
  "validation_status": "PASS | FAIL",
  "errors": [],
  "debug_log": "<internal_reasoning_trace>"
}
```

No step proceeds unless `validation_status == "PASS"`.

## Validation gates (hard stops)

1. **GATE 1 — Parser**: valid FinancialEvent(s), numbers normalized, multiple
   transactions split. FAIL -> STOP.
2. **GATE 2 — Auditor**: correct intent, payer/receiver fixed, ambiguity flagged
   not guessed. FAIL -> STOP.
3. **GATE 3 — Integrity**: all required fields present, event_type valid, amounts
   normalized, split correctness. FAIL -> STOP.
4. **GATE 4 — Rule Engine consistency**: no invalid loan/expense/income mixing,
   cash-flow consistency. FAIL -> STOP.

## Fail-safe rule

If an agent output violates its skill: reject -> regenerate with the same agent ->
log in the debug system. No silent failures.

## Hot reload

Skill files are loaded at runtime. Editing a skill file changes agent behavior on
the next run with no code change.

## FinancialEvent schema (canonical)

transaction_id, intent, event_type, amount, currency, datetime, merchant,
category (optional), participants, payer, receiver, split_details, loan_reference,
budget_reference, notes, confidence_score, original_input, parser_version,
auditor_version.
