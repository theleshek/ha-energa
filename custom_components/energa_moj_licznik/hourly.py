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


def storage_steps(
    plus: list[ChartPoint],
    minus: list[ChartPoint],
    after: datetime | None,
    value: float,
    ratio: float,
    tz: tzinfo = timezone.utc,
) -> tuple[float, datetime | None, float, float]:
    """Zasymuluj magazyn energii u operatora, doba po dobie (doba lokalna ``tz``).

    1. Dla każdej godziny liczymy bilans A+ - A- (dodatni = niedobór, ujemny = nadwyżka).
    2. Bilanse godzinowe sumujemy w dobie. Gdy suma jest ujemna (powstała energia oddana),
       trafia do magazynu pomniejszona o współczynnik ``ratio`` (np. 0.7); gdy dodatnia,
       jest pobierana z magazynu (nie więcej niż w nim jest).

    Doba jest rozliczana dopiero, gdy jest kompletna (wszystkie jej godziny kompletne w obu
    kierunkach) - współczynnik nie jest liniowy względem doby. Godziny ``<= after`` pomijamy.
    Zwraca (nowy stan, ostatnia godzina rozliczonej doby, dopisane do magazynu, pobrane z magazynu).
    """
    p = {pt.start: pt for pt in plus if pt.complete}
    m = {pt.start: pt for pt in minus if pt.complete}
    days: dict[date, list[tuple[datetime, float]]] = {}
    for hour in sorted(set(p) & set(m)):
        if after is not None and hour <= after:
            continue
        net = point_value(p[hour], None) - point_value(m[hour], None)
        days.setdefault(hour.astimezone(tz).date(), []).append((hour, net))

    last = after
    credited = drawn = 0.0
    for day in sorted(days):
        hours = days[day]
        start = datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc)
        end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)
        if after is not None:
            start = max(start, after + timedelta(hours=1))  # po wcześniejszym stanie częściowej doby
        expected = round((end - start).total_seconds() / 3600)  # 24, w dniach zmiany czasu 23/25
        if len(hours) != expected or (last is not None and hours[0][0] - last > timedelta(hours=1)):
            break  # doba niekompletna albo luka - nie rozliczamy
        net = sum(n for _, n in hours)
        if net < 0:
            added = ratio * -net
            value += added
            credited += added
        else:
            taken = min(net, value)
            value -= taken
            drawn += taken
        last = hours[-1][0]
    return round(value, 5), last, round(credited, 5), round(drawn, 5)


def latest_common_hour(
    plus: list[ChartPoint], minus: list[ChartPoint], tz: tzinfo = timezone.utc
) -> datetime | None:
    """Ostatnia godzina ostatniej doby kompletnej w obu kierunkach."""
    return storage_steps(plus, minus, None, 0.0, 0.0, tz)[1]
