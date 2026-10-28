"""Optional AI context for a signal that has already been surfaced.

The model is never asked whether to surface an account. It receives only
aggregated fields that are already on the evaluation (no raw events, no user
identities) and returns a short explanation for the seller. If it is off,
fails, or times out, the decision is unchanged and the seller still sees the
deterministic evidence.

`TemplateSummarizer` is the default and needs no model. Any object with a
`summarize(payload: dict) -> str` method can replace it.
"""
from __future__ import annotations

from typing import Optional, Protocol

from .models import Decision, Evaluation

ALLOWED_FIELDS = ("strength", "confidence", "confidence_reasons", "families")


class Summarizer(Protocol):
    def summarize(self, payload: dict) -> str: ...


class TemplateSummarizer:
    name = "template"

    def summarize(self, payload: dict) -> str:
        fired = [f for f in payload["families"] if f["fired"]]
        parts = "; ".join(f"{f['family']}: {f['evidence'][0]}" for f in fired)
        return (f"{payload['strength']} strength, {payload['confidence']} confidence. "
                f"What changed: {parts}. Trust caveats: {', '.join(payload['confidence_reasons'])}.")


def build_payload(ev: Evaluation) -> dict:
    d = ev.to_dict()
    return {k: d[k] for k in ALLOWED_FIELDS}


def attach_context(ev: Evaluation, summarizer: Optional[Summarizer]) -> Evaluation:
    if summarizer is None:
        ev.ai_context = {"used": False, "reason": "AI context disabled"}
        return ev
    if ev.decision != Decision.SURFACE:
        ev.ai_context = {"used": False, "reason": f"decision is {ev.decision.value}; AI only runs on surfaced signals"}
        return ev
    payload = build_payload(ev)
    try:
        text = summarizer.summarize(payload)
    except Exception as exc:  # the decision never depends on this call
        ev.ai_context = {"used": False, "reason": f"summarizer failed: {type(exc).__name__}"}
        return ev
    ev.ai_context = {
        "used": True,
        "scope": "seller-facing summary of an already-made decision",
        "summarizer": getattr(summarizer, "name", type(summarizer).__name__),
        "fields_sent": list(ALLOWED_FIELDS),
        "text": text,
    }
    return ev
