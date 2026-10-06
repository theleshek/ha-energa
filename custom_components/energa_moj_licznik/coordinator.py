"""Koordynator aktualizacji danych."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import PORTAL_TZ, EnergaAuthError, EnergaClient, EnergaError, Meter, Readings
from .const import DEFAULT_SCAN_INTERVAL, DEFAULT_STORAGE_PERIOD, DEFAULT_STORAGE_RATIO, DOMAIN
from .energy_store import EnergyStore
from .hourly import ChartPoint, DailyUsage, MonthlyBalance, monthly_balance
from .stats import async_sync_hourly

_LOGGER = logging.getLogger(__name__)


@dataclass
class MeterData:
    """Dane jednego licznika: stany oraz zużycie dziś/wczoraj ('A+', 'A-')."""

    readings: Readings = field(default_factory=Readings)
    daily: dict[str, dict[str, DailyUsage]] = field(default_factory=dict)
    monthly: dict[str, MonthlyBalance] = field(default_factory=dict)  # 'this' / 'previous' (tylko prosument)


class EnergaCoordinator(DataUpdateCoordinator[dict[str, MeterData]]):
    """Pobiera odczyty i dane godzinowe dla wybranych PPE (klucz: id licznika)."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: EnergaClient,
        meters: list[Meter],
        entry_id: str = "",
        storage_ratios: dict[str, float] | None = None,
        storage_months: int = DEFAULT_STORAGE_PERIOD,
    ) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_SCAN_INTERVAL)
        self.client = client
        self.last_refresh = None  # czas ostatniego udanego pobrania z portalu (UTC)
        self.meters = meters
        # magazyn energii u operatora - tylko dla prosumentów (klucz: id licznika)
        ratios = storage_ratios or {}
        self.storages = {
            m.id: EnergyStore(hass, entry_id, m.ppe, ratios.get(m.id, DEFAULT_STORAGE_RATIO) / 100, storage_months)
            for m in meters
            if m.prosumer
        }

    async def async_load_storages(self) -> None:
        for store in self.storages.values():
            await store.async_load()

    async def _async_storage_points(
        self, meter: Meter, store: EnergyStore, plus: list[ChartPoint], minus: list[ChartPoint]
    ) -> tuple[list[ChartPoint], list[ChartPoint]]:
        """Dołącz starsze godziny, których magazyn potrzebuje (np. początek okresu przy pierwszym starcie)."""
        needed = store.needed_from()
        available = [p.start for p in plus + minus]
        first = min(available).astimezone(PORTAL_TZ).date() if available else datetime.now(PORTAL_TZ).date()
        if needed >= first:
            return plus, minus
        _LOGGER.info("Magazyn energii: dociągam dane godzinowe od %s dla %s", needed, meter.name)
        extra_plus: list[ChartPoint] = []
        extra_minus: list[ChartPoint] = []
        day = needed
        while day < first:
            extra_plus += await self.client.async_get_day_chart(meter, "A+", day)
            extra_minus += await self.client.async_get_day_chart(meter, "A-", day)
            day += timedelta(days=1)
            await asyncio.sleep(0.2)  # nie męczymy portalu przy dogrywaniu wielu dni
        return extra_plus + plus, extra_minus + minus

    async def _async_monthly_balance(self, meter: Meter) -> dict[str, MonthlyBalance]:
        """Bilans (A+ - A-) bieżącego i poprzedniego miesiąca z wykresu rocznego portalu."""
        today = datetime.now(PORTAL_TZ).date()
        this_month = today.replace(day=1)
        previous_month = (this_month - timedelta(days=1)).replace(day=1)
        points = await self.client.async_get_year_chart(meter, "BP", this_month.year)
        if previous_month.year != this_month.year:  # styczeń: poprzedni miesiąc jest w zeszłym roku
            previous_points = await self.client.async_get_year_chart(meter, "BP", previous_month.year)
        else:
            previous_points = points
        result: dict[str, MonthlyBalance] = {}
        if (value := monthly_balance(points, this_month, PORTAL_TZ)) is not None:
            result["this"] = value
        if (value := monthly_balance(previous_points, previous_month, PORTAL_TZ)) is not None:
            result["previous"] = value
        return result

    async def _async_update_data(self) -> dict[str, MeterData]:
        try:
            await self.client.async_login()
            try:
                readings = await self.client.async_get_readings()
            except EnergaError as err:
                _LOGGER.warning("Sesja portalu wygasła lub odpowiedź niepoprawna (%s), loguję ponownie", err)
                await self.client.async_login()
                readings = await self.client.async_get_readings()
        except EnergaAuthError as err:
            raise ConfigEntryAuthFailed from err
        except EnergaError as err:
            raise UpdateFailed(str(err)) from err

        previous = self.data or {}
        data: dict[str, MeterData] = {}
        for meter in self.meters:
            current = readings.get(meter.id, Readings())
            if not current.values:
                _LOGGER.warning("Portal nie zwrócił odczytów dla licznika %s (%s)", meter.name, meter.ppe)
            daily = previous[meter.id].daily if meter.id in previous else {}
            monthly = previous[meter.id].monthly if meter.id in previous else {}
            try:
                daily, points = await async_sync_hourly(self.hass, self.client, meter)
                store = self.storages.get(meter.id)
                if store and "A+" in points and "A-" in points:
                    plus, minus = await self._async_storage_points(meter, store, points["A+"], points["A-"])
                    await store.async_apply(plus, minus)
            except EnergaError as err:
                # brak danych godzinowych nie powinien wyłączać sensorów ze stanem licznika
                _LOGGER.warning("Nie udało się pobrać danych godzinowych dla %s: %s", meter.name, err)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Nieoczekiwany błąd importu danych godzinowych dla %s", meter.name)
            if meter.prosumer:
                try:
                    monthly = await self._async_monthly_balance(meter)
                except EnergaError as err:
                    _LOGGER.warning("Nie udało się pobrać bilansu miesięcznego dla %s: %s", meter.name, err)
            data[meter.id] = MeterData(readings=current, daily=daily, monthly=monthly)
        self.last_refresh = dt_util.utcnow()
        return data
