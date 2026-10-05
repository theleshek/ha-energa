"""Magazyn energii u operatora (system opustów): oddana energia wraca w części (np. 70 %).

Stan jest liczony z godzinowych danych portalu (hourly.storage_steps) i trwale zapisywany,
bo portal nie podaje stanu magazynu. Użytkownik może go ustawić lub wyzerować.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import PORTAL_TZ
from .const import DOMAIN
from .hourly import ChartPoint, latest_common_hour, storage_steps

_LOGGER = logging.getLogger(__name__)
_VERSION = 1


class EnergyStore:
    def __init__(self, hass: HomeAssistant, entry_id: str, ppe: str, ratio: float) -> None:
        self._store: Store = Store(hass, _VERSION, f"{DOMAIN}.storage_{entry_id}_{ppe}".lower())
        self.ratio = ratio
        self.value = 0.0
        self.last_hour: datetime | None = None  # ostatnia godzina rozliczonej doby (stan na koniec tej doby)
        self.credited = 0.0  # dopisane do magazynu od ostatniego ustawienia/zerowania
        self.drawn = 0.0  # pobrane z magazynu od ostatniego ustawienia/zerowania
        self.changed_at: datetime | None = None  # ostatnie ręczne ustawienie

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.value = float(data.get("value", 0.0))
        self.credited = float(data.get("credited", 0.0))
        self.drawn = float(data.get("drawn", 0.0))
        self.last_hour = dt_util.parse_datetime(data["last_hour"]) if data.get("last_hour") else None
        self.changed_at = dt_util.parse_datetime(data["changed_at"]) if data.get("changed_at") else None

    async def _save(self) -> None:
        await self._store.async_save(
            {
                "value": self.value,
                "credited": self.credited,
                "drawn": self.drawn,
                "last_hour": self.last_hour.isoformat() if self.last_hour else None,
                "changed_at": self.changed_at.isoformat() if self.changed_at else None,
            }
        )

    async def async_apply(self, plus: list[ChartPoint], minus: list[ChartPoint]) -> bool:
        """Uwzględnij nowe godziny. Zwraca True, gdy stan się zmienił."""
        if self.last_hour is None:
            # pierwsze uruchomienie: bez cofania się w czasie, liczymy od ostatniej kompletnej godziny
            self.last_hour = latest_common_hour(plus, minus, PORTAL_TZ)
            if self.last_hour is not None:
                _LOGGER.info("Magazyn energii: start od %s, stan %.3f kWh", self.last_hour, self.value)
                await self._save()
            return False
        if plus and minus:
            first = min(min(p.start for p in plus), min(p.start for p in minus))
            if first > self.last_hour + timedelta(hours=1):
                _LOGGER.warning(
                    "Magazyn energii: brakuje danych godzinowych między %s a %s - stan może być zaniżony/zawyżony; ustaw go ręcznie",
                    self.last_hour,
                    first,
                )
        value, last, credited, drawn = storage_steps(plus, minus, self.last_hour, self.value, self.ratio, PORTAL_TZ)
        if last == self.last_hour:
            return False
        self.value, self.last_hour = value, last
        self.credited = round(self.credited + credited, 5)
        self.drawn = round(self.drawn + drawn, 5)
        await self._save()
        return True

    async def async_set(self, value: float) -> None:
        """Ustaw stan magazynu (kWh); zeruje liczniki dopisane/pobrane."""
        self.value = round(max(0.0, float(value)), 5)
        self.credited = self.drawn = 0.0
        self.changed_at = dt_util.utcnow()
        await self._save()
