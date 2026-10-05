"""Import statystyk godzinowych do rejestratora Home Assistanta (statystyki zewnętrzne).

Dzięki temu panel Energia i wykresy pokazują zużycie godzina po godzinie,
a nie jednym skokiem o północy (jak sensory ze stanem licznika).
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
import logging

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.core import HomeAssistant

from .api import PORTAL_TZ, EnergaClient, Meter
from .const import BACKFILL_DAYS, DOMAIN, MAX_CATCHUP_DAYS, SERIES
from .hourly import ChartPoint, DailyUsage, accumulate, daily_usage

_LOGGER = logging.getLogger(__name__)


def statistic_id(meter: Meter, key: str) -> str:
    return f"{DOMAIN}:{meter.ppe}_{key}".lower()


def _metadata(name: str, stat_id: str) -> StatisticMetaData:
    """Metadane zgodne z różnymi wersjami HA (pola mean_type/unit_class bywają wymagane)."""
    meta: dict = {
        "has_sum": True,
        "name": name,
        "source": DOMAIN,
        "statistic_id": stat_id,
        "unit_of_measurement": "kWh",
    }
    fields = getattr(StatisticMetaData, "__annotations__", {})
    if "has_mean" in fields:
        meta["has_mean"] = False
    if "mean_type" in fields:
        from homeassistant.components.recorder.models import StatisticMeanType

        meta["mean_type"] = StatisticMeanType.NONE
    if "unit_class" in fields:
        meta["unit_class"] = "energy"
    return meta  # type: ignore[return-value]


async def _last_statistic(hass: HomeAssistant, stat_id: str) -> tuple[datetime | None, float]:
    """Początek ostatniej zaimportowanej godziny i suma narastająca (albo None, 0)."""
    result = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, stat_id, True, {"sum"}
    )
    rows = result.get(stat_id) or []
    if not rows:
        return None, 0.0
    start = rows[0]["start"]
    if isinstance(start, (int, float)):
        start = datetime.fromtimestamp(start, tz=timezone.utc)
    return start, float(rows[0].get("sum") or 0.0)


async def async_sync_hourly(
    hass: HomeAssistant, client: EnergaClient, meter: Meter
) -> tuple[dict[str, dict[str, DailyUsage]], dict[str, list[ChartPoint]]]:
    """Pobierz brakujące godziny, zaimportuj statystyki, zwróć zużycie dziś i wczoraj.

    Zwraca ({'A+': {'today': ..., 'yesterday': ...}, 'A-': {...}}, {'A+': punkty, 'A-': punkty});
    A- tylko dla prosumenta.
    """
    today = datetime.now(PORTAL_TZ).date()
    directions = ["A+"] + (["A-"] if meter.prosumer else [])
    usage: dict[str, dict[str, DailyUsage]] = {}
    all_points: dict[str, list[ChartPoint]] = {}

    for mo in directions:
        series = [s for s in SERIES if s[0] == mo]
        last = {key: await _last_statistic(hass, statistic_id(meter, key)) for _, _, key, _ in series}
        starts = [start for start, _ in last.values()]
        if any(s is None for s in starts):
            first_day = today - timedelta(days=BACKFILL_DAYS)
        else:
            first_day = min(starts).astimezone(PORTAL_TZ).date()  # type: ignore[union-attr]
        # zawsze pobieramy wczoraj i dziś (sensory dzienne) i nie cofamy się za daleko
        first_day = min(max(first_day, today - timedelta(days=MAX_CATCHUP_DAYS)), today - timedelta(days=1))

        points: list[ChartPoint] = []
        day: date = first_day
        while day <= today:
            points += await client.async_get_day_chart(meter, mo, day)
            day += timedelta(days=1)
            if day <= today:
                await asyncio.sleep(0.2)  # nie męczymy portalu przy dogrywaniu wielu dni

        for _, zone, key, label in series:
            after, base = last[key]
            rows = accumulate(points, after, base, zone)
            if not rows:
                continue
            stat_id = statistic_id(meter, key)
            async_add_external_statistics(
                hass,
                _metadata(f"{meter.name} – {label}", stat_id),
                [StatisticData(start=start, state=cum, sum=cum) for start, _value, cum in rows],
            )
            _LOGGER.info("Zaimportowano %d godzin do statystyki %s (do %s)", len(rows), stat_id, rows[-1][0])

        all_points[mo] = points
        usage[mo] = {
            "today": daily_usage(points, today, PORTAL_TZ),
            "yesterday": daily_usage(points, today - timedelta(days=1), PORTAL_TZ),
        }
    return usage, all_points
