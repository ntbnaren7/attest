# Sandbox seed run `20261120_094955`: detailed analysis

Seeded by `cashfree/seed.py`. Verified by `cashfree/analyze_seed.py`, which re-reads everything from the live sandbox API as an agent would.
Files in this folder: `truth.json` (ground truth: **agents must not read it**), `payout_requests.json` (queue for the payout policy agent), `analysis.json`, `log.json`.
Every order carries `order_tags.seed_run = 20261120_094955`.

## Headline numbers

| | Value |
|---|---|
| Orders seeded | 41; observed state matches ground truth on **41/41** |
| GMV attempted / collected / at risk | ₹149,971.18 / ₹51,559.55 / **₹98,411.63** (66% not collected) |
| Gateway fees + GST on collections | ₹979.62 + ₹176.32 |
| Refunds attempted / accepted / blocked by Cashfree | 11 / 8 / 3; total refunded ₹7,536.20 |
| Payment links | 5 |
| Payouts executed | 12; paid out ₹180,672.31; fees incl. GST ₹102.66 |
| Payout wallet | ₹99,936,207.90 → ₹99,755,432.93 (change ₹180,774.97 = payouts + fees, **reconciles to the paisa**) |
| Payout requests queued for the policy agent | 8 |

## Collections (recovery agent input)

| Scenario | Orders | Amount | What the API shows | Taxonomy class | Expected action |
|---|---|---|---|---|---|
| paid | 16 | ₹33,239.75 | SUCCESS | - | none |
| failed_insufficient_funds | 4 | ₹8,270.37 | FAILED CUSTOMER_AUTH_FAILURE/insufficient_funds | ASK_CUSTOMER | ASK_CUSTOMER |
| failed_bank_offline | 4 | ₹12,256.42 | FAILED NETWORK_ERROR/issuer_bank_offline | RETRY_LATER | RETRY_LATER |
| failed_user_decline | 3 | ₹9,811.31 | FAILED TRANSACTION_DECLINED/decline_originating_from_user | ASK_CUSTOMER | ASK_CUSTOMER |
| paid_but_unsettled | 3 | ₹12,908.14 | SUCCESS | - | none |
| user_dropped | 3 | ₹4,133.01 | USER_DROPPED | ASK_CUSTOMER | ASK_CUSTOMER |
| abandoned_no_attempt | 3 | ₹6,012.93 | NO_PAYMENT | ASK_CUSTOMER | ASK_CUSTOMER |
| recovered_after_retry | 2 | ₹5,411.66 | SUCCESS | - | none_already_recovered |
| retries_exhausted | 1 | ₹4,506.84 | FAILED NETWORK_ERROR/issuer_bank_offline | RETRY_LATER | PAYMENT_LINK |
| high_value_failed | 1 | ₹48,000.00 | FAILED CUSTOMER_AUTH_FAILURE/insufficient_funds | ASK_CUSTOMER | ASK_CUSTOMER_PRIORITY |
| collect_expired | 1 | ₹5,420.75 | FAILED CUSTOMER_CANCELLED/request_expired | ASK_CUSTOMER | ASK_CUSTOMER |

Every unpaid order stays `ACTIVE` ({'ACTIVE': 20}), so recovery can retry on the same order. No new order is needed.
`retries_exhausted` classifies as RETRY_LATER, and the ZEN strategy turns that into PAYMENT_LINK because attempts ≥ 3. `high_value_failed` (₹48,000) classifies correctly; priority comes from amount-based ranking in the strategy layer.

## Settlements and fees (leakage + recon input)

- The fee rate on this account is **1.9%**, while the public pricing page says 1.95%. **The rate card must come from the merchant's contract, not the website.**
- Cashfree computes fee and GST with **float `round(x, 2)`**. Python's `round(amount*0.019, 2)` and `round(fee*0.18, 2)` reproduce the fee and GST on **21/21** orders. Strict Decimal half-up gets GST wrong on 2/21 (fee ₹9.75 → GST ₹1.75, not ₹1.76). **Use Cashfree-compatible rounding or allow ±1 paisa; otherwise the leakage agent raises about 10% false alarms.**
- `net = amount − fee − GST` holds on 21/21 orders.
- **Leak found: paid but never settled.** 3 orders (₹12,908.14) were captured but are in no settlement. `cf_settlement_id` is null for them, while the other 18 have a settlement ID and UTR.
- `POST /pg/settlement/recon` returns per-payment events (amount, service charge, tax, settlement amount, customer) with cursor pagination. **This is the reconciliation agent's discovery feed.** It has PAYMENT events only for now.

## Refunds (leakage input)

| Case | Result |
|---|---|
| 3 partial + 2 full legitimate refunds | Accepted (sandbox processes them later) |
| over_refund_attempt (₹908.02) | **Blocked by Cashfree** |
| refund_then_exceed_2nd (₹521.60) | **Blocked by Cashfree** |
| refund_on_failed_order (₹2,924.58) | **Blocked by Cashfree** |
| **duplicate_refund_2nd** (₹205.28) | **Accepted**: 2 same-amount refunds visible. Cashfree does not catch this, so **our leakage agent must** |

## Payment links

- link_active_unpaid: ₹4,768.04 → `ACTIVE`, expires 2026-12-20T09:52:04+05:30 (expected action: REMIND)
- link_active_unpaid: ₹3,487.47 → `ACTIVE`, expires 2026-12-20T09:52:05+05:30 (expected action: REMIND)
- link_cancelled: ₹1,094.69 → `CANCELLED`, expires 2026-12-20T09:52:05+05:30 (expected action: NONE)
- link_short_expiry: ₹2,303.48 → `ACTIVE`, expires 2026-11-20T10:12:06+05:30 (expected action: NEW_LINK_AFTER_EXPIRY)
- link_short_expiry: ₹3,236.91 → `ACTIVE`, expires 2026-11-20T10:12:07+05:30 (expected action: NEW_LINK_AFTER_EXPIRY)

## Payouts (payout agent input)

| Kind | Amount | Status now | Fee + GST |
|---|---|---|---|
| vendor_payout | ₹15,019.23 | SUCCESS/COMPLETED | ₹12.0 + ₹2.16 |
| vendor_payout | ₹23,385.84 | SUCCESS/COMPLETED | ₹12.0 + ₹2.16 |
| vendor_payout | ₹3,176.96 | SUCCESS/COMPLETED | ₹7.0 + ₹1.26 |
| vendor_payout | ₹20,091.74 | SUCCESS/SENT_TO_BENEFICIARY | ₹12.0 + ₹2.16 |
| vendor_payout | ₹7,098.54 | SUCCESS/COMPLETED | ₹8.0 + ₹1.44 |
| duplicate_1st | ₹18,450.00 | SUCCESS/COMPLETED | ₹12.0 + ₹2.16 |
| duplicate_2nd_LEAK | ₹18,450.00 | SUCCESS/COMPLETED | ₹12.0 + ₹2.16 |
| over_limit_no_approval | ₹75,000.00 | SUCCESS/COMPLETED | ₹12.0 + ₹2.16 |
| failed_upi | ₹850.00 | FAILED/INVALID_ACCOUNT_FAIL | ₹0.0 + ₹0.0 |
| failed_invalid_account | ₹4,200.00 | FAILED/INVALID_ACCOUNT_FAIL | ₹0.0 + ₹0.0 |
| stuck_pending | ₹9,600.00 | PENDING/ERROR_FETCHING_STATUS | ₹0.0 + ₹0.0 |
| success_then_reversed | ₹12,300.00 | REVERSED/RETURNED_FROM_BENEFICIARY | ₹0.0 + ₹0.0 |

- **Duplicate payout: both legs succeeded** (2 × ₹18,450.00, same vendor and invoice, 2 s apart). Cashfree has no duplicate protection, so **the policy agent must block before submitting.**
- **₹75,000 went out with no approval step.** Limits have to be enforced by us.
- The reversal arrived as `REVERSED / RETURNED_FROM_BENEFICIARY` within about 20 minutes, and the fee was reversed too.
- Fee schedule seen: {'₹12.0 + ₹2.16 GST': 6, '₹7.0 + ₹1.26 GST': 1, '₹8.0 + ₹1.44 GST': 1}. GST of 18% applies to payout fees too.
- API validation rules: `beneficiary_name` rejects `:` and `transfer_remarks` rejects `-`. `cf.transfer` now sanitises remarks.

### Payout request queue (`payout_requests.json`) for the policy agent to decide

- REQ-01: Meena Logistics, ₹6,400.00 → expected **APPROVE** (known verified vendor, within limit)
- REQ-02: Sharma Dairy Pvt Ltd, ₹18,450.00 → expected **BLOCK** (duplicate of an already-paid invoice)
- REQ-03: Patel Cold Storage, ₹82,000.00 → expected **REVIEW** (above ₹50k auto-approve limit)
- REQ-04: Ramesh Kumar, ₹3,000.00 → expected **REVIEW** (name at bank (JOHN SNOW per UPI penny-drop) does not match payee)
- REQ-05: Suresh Traders, ₹5,000.00 → expected **BLOCK** (IFSC invalid / not in registry)
- REQ-06: Ashiq Ahmed Nengroo, ₹2,500.00 → expected **BLOCK** (payee on Indian MHA banned list (OpenSanctions))
- REQ-07: Ravi Kumar Delivery Partner, ₹1,150.00 → expected **APPROVE** (routine partner payout)
- REQ-08: Gupta Hardware, ₹9,600.00 → expected **REVIEW** (previous payout to this payee still PENDING: resolve before paying again)

## Sandbox limitations found

1. **No durable PENDING payment.** A simulated PENDING resolves within seconds, and `testexpired@gocash` expires to `FAILED / CUSTOMER_CANCELLED`. Stuck *payouts* do exist (`PENDING / ERROR_FETCHING_STATUS`).
2. `simulate` accepts only some error codes (`ISSUER_NOT_AVAILABLE` works). Other failures come from the test VPAs `testinsufficientfunds@gocash` and `testdeclineuser@gocash`.
3. The settlement recon feed has no refund events yet, because refunds stay PENDING in sandbox for a while.

## Fixes made during seeding

- `cf.py`: network retry (a TLS EOF crashed the first run; retrying is safe because every create call carries a unique ID), a remarks sanitiser, and v1 balance / fund-source / recharge helpers.
- `taxonomy.py` v3: added Cashfree error codes and `error_source`. Before this, "Issue originating from user… try again" was classed as a bank glitch. Regression run: BBPS 83%, UPI 100%, 0 unsafe retries (unchanged).
