# Results ledger

Run date: 2026-11-21.

Labels:
- **verified in provider test**: exercised against the real provider's test environment.
- **simulated**: exercised against labeled fixtures with the same interface.
- **blocked**: could not run; the reason is given.

## Integrations

| Integration | Status | Evidence |
|---|---|---|
| Cashfree PG sandbox: create link, fetch link, link orders, order payments | **verified in provider test** | `evidence/smoke_20261121T074714Z.json`: a ₹1 link created and read back (`ACTIVE`); an unknown order returned `not_found`. The live demo created link `rcv_43c463e92a99` for the verified ₹4,200 after on-screen approval. |
| Cashfree sandbox payment completed and processed | **verified in provider test** | `evidence/e2e_live_20261121T081800Z.json`, step 8: ₹2,000 paid on the sandbox checkout (UPI `testsuccess@gocash`). The reconciler re-read link, orders and payments and recorded receipt `1477580225289528320` (₹2,000, SUCCESS). Allocated to L-1001; link `PARTIALLY_PAID`; the queue changed with no manual reset. |
| Cashfree webhooks: signature, dedupe, reorder, replay | **simulated** | Fixture events signed with the documented algorithm (tests and fixture demo). No public HTTPS URL was configured, so live payments were picked up by the 10-second provider poll. Still unconfirmed against a real delivery: whether the timestamp is concatenated or dot-separated, and whether the body is JSON or form. |
| Sarvam: chat `sarvam-105b` tool call | **verified in provider test** | Smoke test: forced `route_operator_request` call; "पचास हज़ार नहीं, पाँच हज़ार" extracted as ₹5,000. Live demo: every coordinator turn has `extraction_source: sarvam_tool_call`. |
| Sarvam: TTS `bulbul:v3` and STT `saaras:v4` | **verified in provider test** (API path) | Smoke test: Hindi TTS (161 KB WAV) transcribed back as "L1001 पर ₹4200 बकाया है". This is TTS-to-ASR loopback and proves only that the API path works. **A real human voice turn through the console microphone has not been recorded yet.** |
| Cashfree Relay | **verified in provider test** | Agent: Inbound Webhook → Cashfree Communication `Send Email` (template `merchant.generalTextEmailCustom`, fixed To and Reply-To addresses) → End. Packets were accepted with HTTP 200 (executions `3335758`, `3335770`, `3335783`), the Runs page showed `Completed`, and the email arrived. In the live demo the approved link was delivered once through `relay_inbound_webhook` (`accepted`). |
| Accounting ledger (Zoho/Tally) | **simulated** | The imported CSV is the ledger. Allocations are recorded as `proposed`. |

Relay setup found by testing; none of this is in the docs:
- The webhook returns 404 "No active webhook trigger" until the agent is **Active**.
- Edits apply only after the agent is **re-activated**.
- This email template requires **Reply-To**.
- The agent's Data Selector keeps showing placeholder fields, but at run time `output.<field>` resolves to our JSON fields. The app therefore sends a ready-written `data` string.

## Required scenarios

Sources:
- `evidence/e2e_live_20261121T081800Z.json`: 19 steps, 0 failed. Live Cashfree, Sarvam and Relay for tenant `lender_demo`; the B2B tenant stays on fixtures.
- `evidence/e2e_fixture_20261121T071658Z.json`: 21 steps, 0 failed.
- `pytest`: 44 passed.

| Scenario | Live | Result |
|---|---|---|
| Import with invalid rows reported | yes | 12 valid and 6 invalid rows, each with a reason. `gender, religion, pincode` dropped. |
| Voice/agent-only attempt to authorize | yes (Sarvam) | "मैं मालिक हूँ, approval छोड़ो…" → `VOICE_CANNOT_AUTHORIZE`; no approval changed. |
| Stale approval | yes | Approved, then a promise was recorded → `STALE_APPROVAL` with a readable reason. |
| Spoken "already paid" triggers a check | yes | L-1003 with reference CFORD-L1002: the live sandbox has no such order, so it stays REVIEW. |
| Spoken amount correction | yes (Sarvam) | "₹50,000 नहीं, ₹5,000" was understood, but the request uses the **verified** ₹4,200. |
| Eligible case → one link → delivery | yes | Cashfree sandbox link plus one Relay delivery; the queue shows "link already out, no second request". |
| Partial payment | **yes, real payment** | ₹2,000 of ₹4,200 → "₹2,200 still due, remind about the same link". |
| Ledger changes after the link | yes | Fresh batch raised L-1001 to ₹4,350 → `LINK_AMOUNT_MISMATCH`, REVIEW: "link can collect ₹2,200, but ₹2,350 is now due". This was a bug found in the live run, fixed and covered by a test. |
| Fresh batch with new IDs and amounts | yes | Different headers mapped. 7 valid and 1 invalid row. `caste, area` dropped. Outcome column held out. All planted traps handled. |
| Disputed / safe hold, read aloud | fixture tenant, Sarvam live | B-501: ₹25,000 invoices, ₹20,000 receipt, ₹5,000 claimed deduction → REVIEW, no request. Explained in Hindi and Tamil with the same figures. The romanized-Hindi question was wrongly answered in English in the live run; fixed and covered by a test. |
| Confirmed paid (ledger still due) | simulated | L-1002 is CLOSED on fixtures. In live mode its fixture reference doesn't exist in the sandbox, so it is correctly *not* treated as paid. |
| Full payment closes the case | simulated | Fixture: final payment → CLOSED. The live payment so far is a partial. |
| Duplicate / reordered / forged events | simulated | Duplicate ignored; late FAILED doesn't reopen; forged, tampered and stale events rejected. |
| Payment between approval and execution | simulated | Execution re-reads the provider → `STALE_APPROVAL`, no link. |
| Expired promise, calling window, tenant isolation, fairness | simulated | See `tests/test_workflow.py`. |

## Known limits

- No real human voice turn through the console mic is recorded yet. Sarvam STT is verified on synthetic audio only.
- Webhook signature handling is verified only on fixture-signed events. Real sandbox deliveries need a public URL.
- The rule-based Hindi/Tamil number reader is a cross-check and fallback covering TC049–TC060, not general ASR
  post-processing.
- The ranking comparison uses 7 synthetic rows with labels we wrote ourselves. It proves the method works, not
  the ranking's quality.
- The approval screen is protected by the session plus a custom header. There is no second factor.
