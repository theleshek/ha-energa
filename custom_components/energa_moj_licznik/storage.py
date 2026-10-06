"""Magazyn energii u operatora (net metering, art. 4 ustawy o OZE) - czysta logika bez Home Assistanta.

Zasady (potwierdzone na fakturach Energi i w ustawie):
- bilans liczony jest w każdej godzinie osobno dla każdej strefy (pobór - oddanie);
- w okresie rozliczeniowym (np. dwumiesięcznym) sumuje się osobno godziny z nadwyżką ("sumy sald ujemnych")
  i godziny z niedoborem ("sumy sald dodatnich"), bez salda dobowego;
- nadwyżka trafia do magazynu pomnożona przez współczynnik (0,8 do 10 kW, 0,7 powyżej) jako partia
  z datą wprowadzenia = koniec okresu; niedobór pomniejsza magazyn w całości;
- pobór najpierw z tej samej strefy (najstarsze partie najpierw, FIFO), potem z drugiej strefy;
- partie ważne 12 miesięcy; to, czego zabrakło, jest płatne ("saldo bieżące").
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any

from .hourly import ChartPoint

ZONES = (1, 2)
VALIDITY_MONTHS = 12
_EPS = 1e-6


def add_months(day: date, months: int) -> date:
    """Dodaj miesiące do daty (dzień przycinany do długości miesiąca)."""
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def period_bounds(day: date, months: int) -> tuple[date, date]:
    """Pierwszy i ostatni dzień okresu rozliczeniowego (okresy liczone od stycznia, np. sty-lut, mar-kwi)."""
    start = date(day.year, (day.month - 1) // months * months + 1, 1)
    return start, add_months(start, months) - timedelta(days=1)


@dataclass
class Lot:
    """Partia energii w magazynie: data wprowadzenia (koniec okresu) i ilość kWh."""

    day: date
    amount: float


def settle(
    lots: dict[int, list[Lot]],
    pos: dict[int, float],
    neg: dict[int, float],
    ratio: float,
    end: date,
) -> tuple[dict[int, list[Lot]], dict[int, float]]:
    """Rozlicz okres kończący się ``end``: zwraca (nowe partie, saldo do zapłaty per strefa).

    ``pos`` - suma godzinowych sald dodatnich (niedobór), ``neg`` - ujemnych (nadwyżka, przed współczynnikiem).
    """
    work = {
        z: sorted(
            (Lot(lot.day, lot.amount) for lot in lots.get(z, []) if add_months(lot.day, VALIDITY_MONTHS) >= end),
            key=lambda lot: lot.day,
        )
        for z in ZONES
    }
    for z in ZONES:
        credit = ratio * neg.get(z, 0.0)
        if credit > _EPS:
            work[z].append(Lot(end, credit))

    def draw(zone: int, need: float) -> float:
        for lot in work[zone]:
            take = min(lot.amount, need)
            lot.amount -= take
            need -= take
            if need <= _EPS:
                return 0.0
        return need

    deficit = {z: draw(z, pos.get(z, 0.0)) for z in ZONES}  # najpierw własna strefa
    for z in ZONES:  # potem nadwyżki drugiej strefy
        other = ZONES[1 - ZONES.index(z)]
        if deficit[z] > _EPS:
            deficit[z] = draw(other, deficit[z])
    result = {z: [lot for lot in work[z] if lot.amount > _EPS] for z in ZONES}
    billed = {z: round(deficit[z], 5) if deficit[z] > _EPS else 0.0 for z in ZONES}
    return result, billed


@dataclass
class StorageState:
    """Stan magazynu: partie po ostatnim rozliczeniu oraz sumy godzinowych sald bieżącego okresu."""

    lots: dict[int, list[Lot]] = field(default_factory=lambda: {z: [] for z in ZONES})
    period_start: date | None = None  # początek okresu, którego sumy trwają w pos/neg
    pos: dict[int, float] = field(default_factory=lambda: {z: 0.0 for z in ZONES})
    neg: dict[int, float] = field(default_factory=lambda: {z: 0.0 for z in ZONES})
    last_hour: datetime | None = None  # ostatnia godzina (UTC) wliczona do pos/neg
    last_billed: dict[int, float] = field(default_factory=lambda: {z: 0.0 for z in ZONES})  # z ostatniego rozliczenia

    def total(self, zone: int) -> float:
        return sum(lot.amount for lot in self.lots[zone])

    def to_dict(self) -> dict[str, Any]:
        return {
            "lots": {str(z): [{"day": lot.day.isoformat(), "amount": lot.amount} for lot in self.lots[z]] for z in ZONES},
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "pos": {str(z): self.pos[z] for z in ZONES},
            "neg": {str(z): self.neg[z] for z in ZONES},
            "last_hour": self.last_hour.isoformat() if self.last_hour else None,
            "last_billed": {str(z): self.last_billed[z] for z in ZONES},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StorageState:
        state = cls()
        for z in ZONES:
            state.lots[z] = [
                Lot(date.fromisoformat(item["day"]), float(item["amount"])) for item in data.get("lots", {}).get(str(z), [])
            ]
            state.pos[z] = float(data.get("pos", {}).get(str(z), 0.0))
            state.neg[z] = float(data.get("neg", {}).get(str(z), 0.0))
            state.last_billed[z] = float(data.get("last_billed", {}).get(str(z), 0.0))
        if data.get("period_start"):
            state.period_start = date.fromisoformat(data["period_start"])
        if data.get("last_hour"):
            state.last_hour = datetime.fromisoformat(data["last_hour"])
        return state


def project(state: StorageState, ratio: float, months: int) -> tuple[dict[int, list[Lot]], dict[int, float]]:
    """Stan i saldo do zapłaty, gdyby bieżący okres zakończył się teraz (bez zmiany ``state``)."""
    if state.period_start is None:
        return {z: [Lot(lot.day, lot.amount) for lot in state.lots[z]] for z in ZONES}, {z: 0.0 for z in ZONES}
    _, end = period_bounds(state.period_start, months)
    return settle(state.lots, state.pos, state.neg, ratio, end)


def needed_from(state: StorageState, today: date, months: int, tz: tzinfo) -> date:
    """Od którego dnia lokalnego potrzebne są dane godzinowe, by kontynuować liczenie."""
    if state.last_hour is not None:
        return (state.last_hour + timedelta(hours=1)).astimezone(tz).date()
    return state.period_start or period_bounds(today, months)[0]


def advance(
    state: StorageState,
    plus: list[ChartPoint],
    minus: list[ChartPoint],
    ratio: float,
    months: int,
    today: date,
    tz: tzinfo,
) -> tuple[bool, datetime | None]:
    """Wlicz nowe kompletne godziny (zmienia ``state``). Zwraca (czy zmieniono, początek luki w danych).

    Godziny muszą następować bez przerw od ostatniej wliczonej (a na starcie od początku okresu);
    przy luce liczenie zatrzymuje się i zwracany jest czas pierwszej brakującej godziny.
    """
    p = {pt.start: pt for pt in plus if pt.complete}
    m = {pt.start: pt for pt in minus if pt.complete}
    if state.period_start is None:
        state.period_start = period_bounds(today, months)[0]
    changed = False
    expected = (
        state.last_hour + timedelta(hours=1)
        if state.last_hour is not None
        else datetime.combine(state.period_start, time.min, tzinfo=tz).astimezone(timezone.utc)
    )
    for hour in sorted(set(p) & set(m)):
        if hour < expected:
            continue
        if hour > expected:
            return changed, expected  # brakuje godzin - nie zgadujemy
        day = hour.astimezone(tz).date()
        start, _ = period_bounds(day, months)
        if start > state.period_start:  # początek nowego okresu: rozlicz poprzedni
            _, end = period_bounds(state.period_start, months)
            state.lots, state.last_billed = settle(state.lots, state.pos, state.neg, ratio, end)
            state.pos = {z: 0.0 for z in ZONES}
            state.neg = {z: 0.0 for z in ZONES}
            state.period_start = start
        for z in ZONES:
            net = _zone_value(p[hour], z) - _zone_value(m[hour], z)
            if net > 0:
                state.pos[z] += net
            else:
                state.neg[z] -= net
        state.last_hour = hour
        expected = hour + timedelta(hours=1)
        changed = True
    return changed, None


def _zone_value(point: ChartPoint, zone: int) -> float:
    idx = zone - 1
    value = point.zones[idx] if idx < len(point.zones) else None
    return value if value is not None else 0.0
