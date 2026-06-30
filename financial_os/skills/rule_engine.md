# SKILL: rule_engine

ROLE:
You are the Financial Rule Engine — the TRUTH LAYER. You are pure deterministic
Python. You are the ONLY component allowed to compute money. NO LLM reasoning runs
here. You receive validated FinancialEvent objects and produce deterministic
financial state.

NOTE: This skill documents the contract the deterministic module must satisfy. The
implementation is Python (no model call). It is listed as a "skill" only so its
rules are versioned and hot-reloadable alongside the agents.

RESPONSIBILITIES:
(the ONLY component allowed to compute these)
- income, expenses
- cash in, cash out, current cash
- loans (given/taken), receivables, payables
- assets, liabilities
- net worth
- per-category and per-period KPIs

FORBIDDEN:
- NEVER interpret natural language.
- NEVER guess intent — only act on validated events.
- NEVER let any KPI be a shortcut of another (each computed independently).
- NEVER compute net worth as income - expenses.

INPUT FORMAT:
{ "validated_events": [ <FinancialEvent that passed GATE 1-3>, ... ],
  "opening_cash": <integer> }

OUTPUT FORMAT:
{
  "stage": "rule_engine",
  "input": "<events snapshot>",
  "output": {
    "income": <int>, "expenses": <int>,
    "cash_in": <int>, "cash_out": <int>, "current_cash": <int>,
    "receivables": <int>, "payables": <int>,
    "assets": <int>, "liabilities": <int>, "net_worth": <int>
  },
  "validation_status": "PASS | FAIL",
  "errors": [],
  "debug_log": "<which events contributed to which KPI>"
}

ACCOUNTING RULES (FINAL TRUTH):
- INCOME (salary, business, freelance, gifts, refunds): increases cash + assets.
  Never touches loans or expenses.
- EXPENSE (food, rent, bills, shopping, subscriptions, gifts given): user share
  only for shared expenses.
- LOAN_TAKEN: NOT income. cash += amount, payables += amount.
- LOAN_GIVEN: NOT expense. cash -= amount, receivables += amount.
- LOAN_REPAYMENT_MADE: NOT expense. cash -= amount, payables -= amount.
- LOAN_REPAYMENT_RECEIVED: NOT income. cash += amount, receivables -= amount.
- SHARED_EXPENSE: expense = user share only; remainder = receivable (if user paid).
- TRANSFER: no income, no expense, no net-worth change.
- SAVING / INVESTMENT: NOT an expense; asset movement only.

CASH FORMULA:
  current_cash = opening_cash + cash_in - cash_out
  cash_in  = income + loans_taken + refunds + collections(repayment_received)
  cash_out = expenses + loans_given + repayments_made + savings + investments

NET WORTH:
  net_worth = assets - liabilities      # NEVER income - expenses
  assets      = current_cash + receivables + savings + investments
  liabilities = payables

KPI RULE:
- Every metric (income, expenses, cash_in, cash_out, assets, liabilities,
  net_worth) is computed independently from the event list. No derived shortcuts.

RULES:
- Rounding error tolerance: 0.01 max, then round to integer where the schema says int.
- If an event has an unknown event_type -> FAIL with error "unknown_event_type".

EXAMPLES:
- [income 80000, expense 1500] -> income 80000, expenses 1500, current_cash 78500,
  net_worth 78500.
- [loan_taken 10000] -> income 0, cash_in 10000, payables 10000, net_worth 0.
- [loan_given 5000] -> expenses 0, cash_out 5000, receivables 5000, net_worth 0.
- [income 100000, expense 20000, loan_given 5000, loan_taken 10000] ->
  cash 85000, assets 90000, liabilities 10000, net_worth 80000.

EDGE CASES:
- Negative cash is allowed (overdraft) — do not clamp.
- Repayment exceeding outstanding balance -> FAIL, "repayment_exceeds_balance"
  (caller must split into repayment + new opposite loan).
- Empty event list -> all KPIs from opening_cash only, validation_status PASS.
