# Reconciliation & leakage engine (teammate's code, vendored)

Copied from https://github.com/MSR-3117/SarvamxCashfree at commit `a991082`. The engine (`agent/`) and its Cashfree
client (`integrations/cashfree/cf.py`) are unchanged. The dashboard drives it through `app/recon.py`.

- `agent/`: recon, leakage and recovery agents plus the LangGraph pipeline (deterministic rules decide, a person approves).
- `runs/seeds/seed_20261120_094955/`: the merchant's books and bank statement for one seeded scenario.
- `data/recorded_cashfree_snapshot.json`: Cashfree responses recorded from the teammate's sandbox account for that
  scenario (41 orders, settlement batches, per-order settlement feed). Those orders exist only in that account, so
  by default the dashboard runs the engine against this recording and labels the results as recorded data.

To refresh from upstream, copy the same paths again and re-run `tests/test_recon.py`.
