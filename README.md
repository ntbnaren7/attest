# Attest

**Payments, proven.** A collections and payments desk that only believes verified money.
Built for the **Sarvam × Cashfree Buildathon** (Problem 2: *Who will pay first?*, plus a merchant/supplier mode).

---

## The problem

- A customer shows a "Payment Successful" screenshot, but no money arrives. The merchant then checks apps, calls support and answers the same messages, for hundreds of transactions a day.
- Lenders chase people who have already paid, call people in hardship, and work from stale lists.
- Notes, promises and receipts live in diaries, chats and photos, not in the system.

## How Attest solves it

- **Claims are not facts.** "I paid", a screenshot or a diary note is recorded as a *claim*. Only money verified by Cashfree changes a balance.
- **The queue ranks who will pay if asked**, with a plain reason on every row: Call now · Wait · Check first · Done.
- **Payments close cases by themselves** through Cashfree webhooks, plus a poller as backup.
- **It speaks the customer's language**: English, हिन्दी, தமிழ், ಕನ್ನಡ, by text, voice or photo.
- **A person approves every money action on screen.** Voice and background jobs can prepare, never approve.

---

## Agents

| Agent | What it does |
|---|---|
| **Orchestrator** (Assistant) | Understands the operator's voice or text (Sarvam `sarvam-105b` tool calls), finds the account by ID or name, and routes to the right agent. Rule checks overrule the model on approvals, amounts and dates. |
| **Evidence** | Checks a claimed payment or reference against Cashfree orders, links and payments. The only way a payment becomes a fact. |
| **Reconciliation** (case) | Records claims, allocates verified receipts and decides each case: contact, wait, check or done. |
| **Priority / risk** | Scores likelihood to pay (0–100) from allow-listed behaviour only, with reasons. Gender, religion, pincode and name are dropped on import. |
| **Recovery** | Prepares a payment link for the verified amount and, after approval, creates it (Cashfree) and delivers it (Relay email). |
| **Customer query** | Replies to customers from that account's verified facts only, in their language, spoken back for voice. Blocks invented amounts and refund promises. |
| **OCR** | Reads screenshots, receipts, slips and whole ledger pages (Sarvam Document AI). Ledger lines update the matching accounts or become new customers. |
| **Reconciliation & leakage engine** | Books vs Cashfree vs bank: unsettled captures, duplicate refunds, missing credits, fee leakage. Fixes are proposed as a dry run for approval. |
| **Recovery daemon** | Every 60 s: ranks the book, picks the next eligible customers inside contact hours, prepares their links for approval, and schedules follow-ups. |
| **Payment watchers** | Verify webhook signatures, de-duplicate events, apply full and partial payments, and re-form the queue live. |

---

## Edge cases handled

| Situation | What happens |
|---|---|
| Fake or unverified payment screenshot | OCR reads the amount and UTR, Cashfree is checked, the case is marked "says paid, not verified", and reminders pause. |
| Payment reference belongs to another account | Marked as an evidence conflict and held for a person. |
| Partial payment | The balance is reduced and only the remainder is asked for. |
| Promise to pay ("salary 5th ko aayega") | Recorded with its date; reminders wait until then. Broken promises are flagged. |
| Hardship or dispute | Collection stops and the case goes to a person with options. |
| "Don't call me" | Do-not-contact is enforced for every message and link. |
| Outside 8 am–7 pm | No outreach (RBI recovery-call window). |
| Spoken self-corrections ("50,000, no, 5,000") | The final amount is used; if two amounts stay plausible, it asks. |
| Voice says "approve it now" | Refused: approval only on screen, bound to case, amount, version and recipient. |
| Waiver or refund request | Polite refusal; a person reviews. |
| Bad rows in an uploaded list | Rejected with the reason; good rows still import. |
| Same webhook twice, or a bad signature | Ignored, or rejected and logged. |
| Account question vs books question | "What is L1001?" gets the account's status; "any settlement issues?" gets the reconciliation summary. |

---

## Dashboard

- **Collections:** the ranked queue, add a customer, scan a ledger page, upload a list, and the voice/text Assistant.
- **Customer chat:** chat as the customer (text, voice note, photo) or as the business (reminder drafted from verified facts).
- **Reconciliation:** run the engine on live Cashfree data, then approve fixes (dry run).
- **Operations:** live feed over server-sent events: every agent step, success rates, recovered revenue, leakage, daemon status.
- **Top bar:** Loan book / Customer invoices, Sample data / Live run, language, theme.

## Try OCR quickly

Sample photos in `fixtures/` (synthetic):
- `upi_screenshot.png`: a "Payment Successful" screenshot for ₹5,000 → **Customer chat → 📎** (Customer invoices). Held as "says paid, not verified".
- `receipt.jpg`: a ₹1,500 UPI receipt → **Customer chat → 📎**.
- `ledger_page.jpg`: a handwritten collection-diary page → **Collections → Scan ledger page** (Loan book). Updates five accounts.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
(cd web && npm ci && npm run build)
cp .env.example .env        # add sandbox keys; without them it runs on fixtures
DB_PATH=data/demo.db .venv/bin/uvicorn app.main:app --port 8000
# open http://localhost:8000 (DEMO_MODE=1 signs in automatically on this machine)
.venv/bin/pytest -q          # 95 tests
```

**Stack:** FastAPI + SQLite, React + TypeScript (Vite), Cashfree PG sandbox, Sarvam (LLM, STT, TTS, Document AI), Relay.
Secrets stay in `.env` (git-ignored). All data is synthetic.

## Team code

- `recon/`: reconciliation & leakage engine, from [MSR-3117/SarvamxCashfree](https://github.com/MSR-3117/SarvamxCashfree)
- `voice/`: customer voice layer, from [DevSanjay09/voice-sarvam](https://github.com/DevSanjay09/voice-sarvam)
- `ocr/`: Sarvam Document AI OCR, from [corefour05-code/sarvam-priority-agent](https://github.com/corefour05-code/sarvam-priority-agent)

More detail: [`docs/DESIGN.md`](docs/DESIGN.md) · [`docs/RESULTS.md`](docs/RESULTS.md)
