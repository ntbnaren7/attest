"""Merchant-specific settings. Every number here is a business rule, not a guess: change them per merchant."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(CORE, "data")
RULES = os.path.join(CORE, "rules")
RUNS = os.path.join(ROOT, "runs")
SEEDS = os.path.join(RUNS, "seeds")      # seeded sandbox scenarios + ground truth (test fixtures)
REPORTS = os.path.join(RUNS, "reports")  # agent / explore / payout-matrix outputs

# Contracted PG pricing for THIS merchant (sandbox account observed 1.9%; Cashfree's public page says 1.95%).
# Always take this from the merchant agreement, never from the website.
PG_FEE_RATE = {"default": 0.019}
GST_RATE = 0.18
MONEY_TOLERANCE = 0.01          # ₹; Cashfree rounds with float round(), so allow 1 paisa on derived values

# Settlement timing. A capture is "in transit" (not an error) until EITHER a later Cashfree settlement has already
# covered payments captured after it (it was skipped), OR it is older than this many hours (contracted cycle, T+2).
SETTLEMENT_SLA_HOURS = 48

# Deadlines attached to findings. Only the RBI TAT is regulatory; the rest are merchant policy: set them per merchant.
RBI_MERCHANT_TAT_DAYS = 5       # RBI DPSS.CO.PD No.629/02.01.014/2019-20: debited, merchant not confirmed -> reversal by T+5
ESCALATION_DAYS = 7             # raise settlement / fee discrepancies with Cashfree or the bank within N days
COLLECTION_DAYS = 7             # chase money for goods already delivered within N days
FULFIL_DAYS = 2                 # customer paid but order not fulfilled: fulfil or refund within N days

# How a bank narration names Cashfree as the payer (used to spot Cashfree credits that match no settlement).
CASHFREE_PAYER_RX = r"CASHFREE|CF ?PAYMENTS|GOCASHFREE"
REVERSAL_RX = r"^\s*(REVERSAL|REV)[-/ ]|\bRETURN(ED)?\b"   # bank debit lines that pull back an earlier credit
DUPLICATE_PAYMENT_WINDOW_MIN = 30  # same customer + same amount paid again within N minutes = likely retry, not a new sale
DISPUTE_RESPONSE_DAYS = 7          # policy: respond to a chargeback/dispute within N days (check Cashfree's respond-by date)
DIRECT_MODES = {"UPI_DIRECT", "BANK_TRANSFER", "CASH"}  # sales in the books that did NOT go through Cashfree

# Settlement <-> bank matching (agent/lib/matching.py; validated on BenchRec real bank data, research/benchrec)
MATCH_POLICY = "reference_required"   # Cashfree credits always carry the UTR. "allow_amount_only" for ledgers without
                                      # reliable references: a same-day, same-amount pair with no look-alike is accepted
MATCH_WINDOW_DAYS = 3
MATCH_MIN_FRAGMENT = 7                # shared reference fragment (>= 5 digits, not filler)
MATCH_MIN_AMOUNT_ONLY_PAISE = 10_000  # amount-only matches only for amounts >= 100 (small amounts repeat by chance)
PAYOUT_AUTO_APPROVE_LIMIT = 50_000.0
STUCK_PAYOUT_MINUTES = 15
SANCTIONS_SCREENING = True       # OpenSanctions data is CC-BY-NC: OK for a demo, needs a commercial licence in production
SANCTIONS_THRESHOLD = 85        # benchmark: 39/40 caught, 0/396 false hits (research/real_data/RESULTS.md)

# expected probability that a failure class is recovered (used only to rank work, not to decide)
P_RECOVER = {"RETRY_LATER": 0.6, "CHECK_STATUS": 0.5, "RETRY_NEXT_DAY": 0.5, "ASK_CUSTOMER": 0.35,
             "NEVER_RETRY": 0.05, "UNKNOWN": 0.2}
