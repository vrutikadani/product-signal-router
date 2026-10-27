"""One account, one day: features + CRM context + engine memory -> decision."""
from __future__ import annotations

from datetime import date
from typing import Optional

from .actionability import check_rules
from .ai_context import Summarizer, attach_context
from .config import LOGIC_VERSION, Config
from .models import AccountFeatures, Band, CrmContext, Decision, Evaluation
from .signals import evaluate_confidence, evaluate_strength
from .state import SignalStore


def evaluate(
    f: AccountFeatures,
    crm: CrmContext,
    store: SignalStore,
    today: date,
    config: Config = Config(),
    summarizer: Optional[Summarizer] = None,
) -> Evaluation:
    families, strength = evaluate_strength(f, config)
    confidence, conf_reasons = evaluate_confidence(f, crm.match_method, today, config)
    rules, notes = check_rules(f, crm, store, today, config, strength)
    passed = [label for ok, label, _ in rules if ok]
    blocking = [(label, dec) for ok, label, dec in rules if not ok]

    if strength == Band.NONE:
        decision = Decision.NO_SIGNAL
    elif strength == Band.LOW:
        decision = Decision.MONITOR
    elif blocking:
        decision = blocking[0][1]
    elif confidence == Band.LOW:
        decision = Decision.HOLD
        blocking = [("confidence too low to spend seller time", Decision.HOLD)]
    else:
        decision = Decision.SURFACE

    ev = Evaluation(
        account_id=f.account_id,
        as_of=f.as_of,
        logic_version=LOGIC_VERSION,
        families=families,
        strength=strength,
        confidence=confidence,
        confidence_reasons=conf_reasons,
        actionable=decision == Decision.SURFACE,
        blocking_reasons=[b[0] for b in blocking],
        rules_passed=passed,
        decision=decision,
        crm_account_id=crm.crm_account_id,
        owner_id=crm.owner_id,
        seller_notes=notes,
    )
    return attach_context(ev, summarizer)
