"""Product Signal Router: account-level product events -> ranked, auditable GTM actions."""
from .config import LOGIC_VERSION, Config
from .engine import evaluate
from .models import AccountFeatures, Band, CrmContext, Decision, Evaluation, ProductEvent
from .state import SignalStore

__all__ = ["LOGIC_VERSION", "Config", "evaluate", "AccountFeatures", "Band", "CrmContext",
           "Decision", "Evaluation", "ProductEvent", "SignalStore"]
