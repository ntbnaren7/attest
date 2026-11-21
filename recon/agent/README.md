# agent: reconciliation & leakage agent core

Four deterministic specialist agents + a LangGraph orchestrator with a human-approval gate.
**Deterministic engines decide, the LLM only explains, a human approves money movement.**

```
load ─┬─ recon ────┐
      ├─ leakage ──┤
      ├─ recovery ─┼─ triage ─ explain (template | Sarvam LLM) ─ approve ⏸ (LangGraph interrupt) ─ execute ─ report
      └─ payouts ──┘
```

## Quick start
```bash
python3 -m venv .venv && .venv/bin/pip install -r agent/requirements.txt   # Python 3.9 OK
.venv/bin/python -m agent.sources.ledger build 20261120_094955     # merchant books + bank statement for a seed run
.venv/bin/python -m agent.cli --run 20261120_094955                # analyse; approve nothing; dry-run
.venv/bin/python -m agent.cli --run 20261120_094955 --approve ask --execute   # approve one by one, really call Cashfree (sandbox)
```
Output: `runs/reports/agent_<ts>/{REPORT.md, findings.json, eval.json}`.
Keys come from `integrations/cashfree/.env` (see `integrations/cashfree/cf.py`). New seed data: `cd sandbox && SEED=<n> python3 seed.py`.

## What each agent does

| Agent | Inputs | Finds | Engine |
|---|---|---|---|
| **recon** | merchant ledger, Cashfree orders/payments/settlements + per-order settlement feed, bank statement | FULFILLED_BUT_UNPAID, PAID_NOT_FULFILLED, AMOUNT_MISMATCH, CAPTURED_NOT_SETTLED, SHORT_CREDIT, EXCESS_CREDIT, MISSING_CREDIT, DUPLICATE_CREDIT, UNEXPLAINED_CASHFREE_CREDIT, BATCH_TOTAL_MISMATCH, TIEOUT_BREAK | OpenRecon (vendored, MIT) + reference-evidence **guard** (exact / truncated / garbled) + paise **tie-out** |
| **leakage** | per-order settlement fees, refunds, payout register | FEE_OVERCHARGE, GST_MISMATCH, NET_SETTLEMENT_SHORT, DUPLICATE_REFUND, OVER_REFUND, DUPLICATE_PAYOUT, PAYOUT_OVER_LIMIT_NO_APPROVAL | rules with Cashfree-compatible rounding (`money.py`) |
| **recovery** | orders the books mark PAID (shipped) + Cashfree payment error details | RECOVER: why the payment failed + collection action (payment link / check status / stop / human review) | `lib/taxonomy.py` (NPCI + Cashfree codes + error_source) + ZEN `rules/recovery_strategy.json`. Ordinary failed/abandoned payments are left to Cashfree Relay |
| **payouts** | payout register (+ live status), request queue | STUCK_PAYOUT, PAYOUT_IN_FLIGHT, REVERSED_PAYOUT, FAILED_PAYOUT, PAYOUT_DECISION (APPROVE/REVIEW/BLOCK) | OpenSanctions screen, IFSC registry, Secure ID UPI name-at-bank + guarded name match, ZEN `rules/payout_policy.json` |

Business rules live in `config.py` (fee rate, limits, thresholds) and `rules/*.json` (ZEN decision tables, editable without code).

## Reconciliation guard rails (tested by `research/recon_audit.py`: 287 planted-error trials)
- **Tie-out in paise**, in four sections:
  - A: books vs captured.
  - B: captured vs settled, plus in transit.
  - C: settled vs bank.
  - C2: every Cashfree-named bank credit is classified.

  A non-zero residual raises `TIEOUT_BREAK`: don't sign off. The tie-out proves *completeness*: every rupee lands in some finding. *Correctness* comes from the scenario tests.
- **Settlement timing:** a capture is in transit until either a later Cashfree settlement has covered payments captured after it, or it's older than `SETTLEMENT_SLA_HOURS`.
- **Breakdown:** every settlement finding carries the batch's per-order lines from `/pg/settlement/recon`, and each batch must add up internally.
- **Deadlines:** every actionable finding has a due date with its basis. The RBI T+5 rule applies only where the payment outcome is unknown; everything else is merchant policy in `config.py`.
- **Counted once:** findings about the same rupees share a `money_key`, and ₹ at stake counts each key once. Examples: recon plus recovery on a shipped-but-unpaid order; a duplicate payout that is also over the limit.

## Safety properties (all tested)
- **Nothing executes without approval**: `needs_approval` findings stop at a LangGraph `interrupt()`; default is dry-run.
- **Idempotent execution**: payouts and recovery links use deterministic IDs, so a re-run or double approval returns `ALREADY_DONE` (verified live: ₹1,150 payout sent exactly once).
- **No blind retries**: no automatic payment retries at all; outcome-unknown failures are status-checked first, unknown codes go to HUMAN_REVIEW.
- **LLM guardrail**: `explain.narrate(findings, llm=...)` rejects any LLM text containing a ₹ amount that isn't in the evidence.

## Plugging in Sarvam (teammates)
```python
from agent.pipeline import graph
graph.LLM = lambda prompt: sarvam_chat(prompt)   # any callable(str) -> str
```
The prompt asks for a brief in a chosen language and copies amounts verbatim; ungrounded output falls back to the template.

## Scores (see runs/reports/agent_*/eval.json)
Seed `20261120_094955` (built alongside the code) and held-out seed `20261120_101710` (different random seed, unseen):
all recon / leakage / recovery / payout-decision checks 100% precision and recall (recovery re-scored 2026-11-21 on the narrowed scope), 0 fee false alarms on real Cashfree
settlements. Payout *audit* depends on timing (sandbox reversals land ~20 min after sending; pending < 15 min is "in flight").

## Known limits
- Cashfree has no "list orders" API in our tests; agents read order IDs from the merchant ledger (as a real merchant DB would) and settlements via `POST /pg/settlement/recon`.
- Sandbox can't hold a payment in PENDING; CHECK_STATUS paths are exercised with payouts only.
- Sanctions data is OpenSanctions (CC-BY-NC): fine for a demo, needs a licence commercially.
