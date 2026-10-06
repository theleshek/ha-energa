"""Logika danych godzinowych i dobowych (bez zależności od Home Assistanta)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo


@dataclass
class ChartPoint:
    """Jedna godzina z wykresu dobowego portalu."""

    start: datetime  # początek godziny, UTC
    zones: list[float | None]  # zużycie w kWh dla stref 1..3 (None = strefa nieaktywna)
    complete: bool  # godzina kompletna (cplt=true i est=false)


@dataclass
class DailyUsage:
    """Zużycie w ciągu doby (tylko kompletne godziny)."""

    day: date
    total: float
    zones: dict[int, float]
    hours: int


@dataclass
class MonthlyBalance:
    """Bilans miesiąca z wykresu rocznego (BP = A+ - A-); ujemny = nadwyżka oddana."""

    month: date  # pierwszy dzień miesiąca
    value: float
    complete: bool


def monthly_balance(points: list[ChartPoint], month: date, tz: tzinfo) -> MonthlyBalance | None:
    """Pozycja wykresu rocznego odpowiadająca miesiącowi ``month`` (pierwszy dzień) albo None."""
    for p in points:
        if p.start.astimezone(tz).date() == month:
            return MonthlyBalance(month=month, value=round(point_value(p, None), 5), complete=p.complete)
    return None


def point_value(point: ChartPoint, zone: int | None) -> float:
    """Wartość godziny dla strefy (1..3) albo suma wszystkich stref, gdy zone=None."""
    if zone is None:
        return sum(v for v in point.zones if v is not None)
    idx = zone - 1
    value = point.zones[idx] if idx < len(point.zones) else None
    return value if value is not None else 0.0


def accumulate(
    points: list[ChartPoint], after: datetime | None, base_sum: float, zone: int | None
) -> list[tuple[datetime, float, float]]:
    """Zwróć (początek godziny, wartość, suma narastająca) dla nowych, kompletnych godzin.

    Przerywamy na pierwszej niekompletnej godzinie, aby suma narastająca nie miała luk
    (portal oznacza jako niekompletne tylko najnowsze godziny).
    """
    result: list[tuple[datetime, float, float]] = []
    total = base_sum
    seen: set[datetime] = set()
    for point in sorted(points, key=lambda p: p.start):
        if point.start in seen:
            continue
        seen.add(point.start)
        if not point.complete:
            break
        if after is not None and point.start <= after:
            continue
        value = point_value(point, zone)
        total += value
        result.append((point.start, round(value, 5), round(total, 5)))
    return result


def daily_usage(points: list[ChartPoint], day: date, tz: tzinfo) -> DailyUsage:
    """Zsumuj kompletne godziny przypadające na dany dzień lokalny."""
    zones: dict[int, float] = {1: 0.0, 2: 0.0, 3: 0.0}
    hours = 0
    seen: set[datetime] = set()
    for p in points:
        if p.start in seen or not p.complete or p.start.astimezone(tz).date() != day:
            continue
        seen.add(p.start)
        hours += 1
        for z in zones:
            zones[z] += point_value(p, z)
    zones = {z: round(v, 5) for z, v in zones.items() if v or z <= 2}
    return DailyUsage(day=day, total=round(sum(zones.values()), 5), zones=zones, hours=hours)
