"""ZEN decision tables (rules/*.json): business policy editable without code changes."""
import os

import zen

from ..config import RULES

_ZEN = zen.ZenEngine()
_decisions = {}


def decide(rule_file, inputs):
    if rule_file not in _decisions:
        _decisions[rule_file] = _ZEN.create_decision(open(os.path.join(RULES, rule_file)).read())
    return _decisions[rule_file].evaluate(inputs)["result"]
