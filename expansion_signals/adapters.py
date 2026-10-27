"""Interfaces to the outside world, plus in-memory versions for tests and the demo.

A real deployment implements these three against its warehouse, its CRM, and
wherever sellers work (a CRM task, a queue view, a chat message). The engine
only depends on the Protocols.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Protocol

from .models import CrmContext, Evaluation, ProductEvent


class WarehouseSource(Protocol):
    def account_ids(self) -> Iterable[str]: ...
    def created_on(self, account_id: str) -> date: ...
    def events(self, account_id: str, through: date) -> list[ProductEvent]: ...
    def loaded_through(self) -> date: ...


class CrmSource(Protocol):
    def context(self, account_id: str) -> CrmContext: ...


class SellerSink(Protocol):
    def deliver(self, ev: Evaluation, signal_key: str) -> None: ...


@dataclass
class InMemoryWarehouse:
    created: dict[str, date]
    rows: list[ProductEvent]
    loaded: date

    def account_ids(self):
        return sorted(self.created)

    def created_on(self, account_id):
        return self.created[account_id]

    def events(self, account_id, through):
        return [e for e in self.rows if e.account_id == account_id and e.day <= through]

    def loaded_through(self):
        return self.loaded


@dataclass
class InMemoryCrm:
    contexts: dict[str, CrmContext]

    def context(self, account_id):
        return self.contexts.get(account_id) or CrmContext(account_id, [], "none")


@dataclass
class ListSink:
    delivered: list[tuple[str, Evaluation]] = field(default_factory=list)

    def deliver(self, ev, signal_key):
        self.delivered.append((signal_key, ev))
