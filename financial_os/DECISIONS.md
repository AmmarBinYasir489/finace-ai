# Financial OS — Architecture Decisions

## ADR-001: Rule Engine is the computation contract; no destructive DB migration

**Date:** 2026-06-30
**Status:** Accepted

### Context

The Financial OS v4.1 spec models loans, shared expenses, transfers, and savings as
first-class `event_type`s that NEVER touch income/expense, with a single
deterministic Rule Engine as the only computation layer.

The existing VoiceTrack DB (`voicetrack/db.py`) instead stores every loan/shared
movement as a real `expense`/`income` row tagged with `kind`, carries a signed
`cash_flow` column, and excludes those `kind`s from income/expense sums. It already
produces spec-correct results:
- net worth = cash + receivables − payables (NOT income − expenses)
- loans/shared excluded from income & expense totals

### Key finding

The two models are **not trivially reconcilable**. When a third party pays a shared
bill, the legacy code stores the user's share as an `expense` row with
`cash_flow = 0` (no cash left the user) and records the payable separately in
`loan_accounts`. A spec-pure Rule Engine treats every expense as `cash_out += amount`.
A naive row→event remap therefore overstates `cash_out`. The legacy `cash_flow`
column encodes cash semantics that a faithful migration must reconstruct
per-event — a backfill project, not a quick remap.

### Decision

1. `financial_os/rule_engine.py` is the **canonical, tested computation contract**
   and the engine for any new event-sourced features. It is the reference
   implementation of the spec's accounting rules.
2. The live `db.get_finance_summary` path is **retained unchanged** — it is proven,
   tested, and already spec-correct in its outputs.
3. **No destructive schema migration** is performed now. The risk/reward is poor:
   the outputs are already correct, and migrating real user data is the highest-risk
   change available.
4. If full event-sourcing is desired later, it is a deliberate Stage 5 with a
   semantics-aware backfill that reconstructs each historical event's true cash
   effect — explicitly out of scope until requested.

### Consequences

- The agent scaffolding (skills, loader, events, rule engine) is **additive**: the
  running app keeps working while the spec architecture is adopted incrementally.
- New work (Memory/Budget/Insight agents, validation gates, skill-driven prompts)
  builds on `rule_engine.py` and the `FinancialEvent` vocabulary.
- The legacy and reference computation paths coexist; tests guard both.
