"""Specialist agents. Each is deterministic: ctx -> list[Finding] with evidence. No LLM decides anything."""
from .leakage import run_leakage
from .payouts import run_payouts
from .recon import run_recon
from .recovery import run_recovery

AGENTS = {"recon": run_recon, "leakage": run_leakage, "recovery": run_recovery, "payouts": run_payouts}
