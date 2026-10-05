"""Koordynator aktualizacji danych."""
from __future__ import annotations

from dataclasses import dataclass, field
import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import EnergaAuthError, EnergaClient, EnergaError, Meter, Readings
from .const import DEFAULT_SCAN_INTERVAL, DEFAULT_STORAGE_RATIO, DOMAIN
from .energy_store import EnergyStore
from .hourly import DailyUsage
from .stats import async_sync_hourly

_LOGGER = logging.getLogger(__name__)


@dataclass
class MeterData:
    """Dane jednego licznika: stany oraz zużycie dziś/wczoraj ('A+', 'A-')."""

    readings: Readings = field(default_factory=Readings)
    daily: dict[str, dict[str, DailyUsage]] = field(default_factory=dict)


class EnergaCoordinator(DataUpdateCoordinator[dict[str, MeterData]]):
    """Pobiera odczyty i dane godzinowe dla wybranych PPE (klucz: id licznika)."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: EnergaClient,
        meters: list[Meter],
        entry_id: str = "",
        storage_ratio: float = DEFAULT_STORAGE_RATIO,
    ) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_SCAN_INTERVAL)
        self.client = client
        self.last_refresh = None  # czas ostatniego udanego pobrania z portalu (UTC)
        self.meters = meters
        # magazyn energii u operatora - tylko dla prosumentów (klucz: id licznika)
        self.storages = {
            m.id: EnergyStore(hass, entry_id, m.ppe, storage_ratio / 100) for m in meters if m.prosumer
        }

    async def async_load_storages(self) -> None:
        for store in self.storages.values():
            await store.async_load()

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
            try:
                daily, points = await async_sync_hourly(self.hass, self.client, meter)
                store = self.storages.get(meter.id)
                if store and "A+" in points and "A-" in points:
                    await store.async_apply(points["A+"], points["A-"])
            except EnergaError as err:
                # brak danych godzinowych nie powinien wyłączać sensorów ze stanem licznika
                _LOGGER.warning("Nie udało się pobrać danych godzinowych dla %s: %s", meter.name, err)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Nieoczekiwany błąd importu danych godzinowych dla %s", meter.name)
            data[meter.id] = MeterData(readings=current, daily=daily)
        self.last_refresh = dt_util.utcnow()
        return data
