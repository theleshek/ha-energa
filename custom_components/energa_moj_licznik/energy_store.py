"""Magazyn energii u operatora (net metering): trwały stan liczony z godzinowych danych portalu.

Portal nie podaje stanu magazynu, więc liczymy go tak jak Energa na fakturze (patrz storage.py)
i zapisujemy w .storage Home Assistanta. Użytkownik ustawia stan początkowy z faktury.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import PORTAL_TZ
from .const import DOMAIN
from .hourly import ChartPoint
from .storage import (
    ZONES,
    Lot,
    StorageState,
    advance,
    needed_from,
    period_bounds,
    project,
)

_LOGGER = logging.getLogger(__name__)
_VERSION = 1


class EnergyStore:
    def __init__(self, hass: HomeAssistant, entry_id: str, ppe: str, ratio: float, months: int) -> None:
        # Nowy klucz: stary model (jedna pula) był inny i jego stanu nie da się przenieść.
        self._store: Store = Store(hass, _VERSION, f"{DOMAIN}.storage2_{entry_id}_{ppe}".lower())
        self.ratio = ratio  # 0.7 / 0.8
        self.months = months  # długość okresu rozliczeniowego
        self.state = StorageState()
        self.changed_at: datetime | None = None  # ostatnie ręczne ustawienie
        self.gap_from: datetime | None = None  # początek luki w danych portalu (jeśli jest)

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.state = StorageState.from_dict(data.get("state", {}))
        self.changed_at = dt_util.parse_datetime(data["changed_at"]) if data.get("changed_at") else None

    async def _save(self) -> None:
        await self._store.async_save(
            {
                "state": self.state.to_dict(),
                "changed_at": self.changed_at.isoformat() if self.changed_at else None,
            }
        )

    def today(self) -> date:
        return datetime.now(PORTAL_TZ).date()

    def needed_from(self) -> date:
        """Od którego dnia potrzebne są dane godzinowe (A+ i A-)."""
        return needed_from(self.state, self.today(), self.months, PORTAL_TZ)

    def projection(self):
        """(partie, saldo do zapłaty) przy założeniu, że bieżący okres kończy się teraz."""
        return project(self.state, self.ratio, self.months)

    def period(self) -> tuple[date, date]:
        return period_bounds(self.state.period_start or self.today(), self.months)

    def default_lot_date(self) -> date:
        """Data partii ustawianej ręcznie: koniec ostatniego rozliczonego okresu."""
        return (self.state.period_start or period_bounds(self.today(), self.months)[0]) - timedelta(days=1)

    async def async_apply(self, plus: list[ChartPoint], minus: list[ChartPoint]) -> bool:
        """Wlicz nowe godziny. Zwraca True, gdy stan się zmienił."""
        changed, gap = advance(self.state, plus, minus, self.ratio, self.months, self.today(), PORTAL_TZ)
        if gap != self.gap_from:
            self.gap_from = gap
            if gap is not None:
                _LOGGER.warning(
                    "Magazyn energii: brakuje danych godzinowych od %s - liczenie wstrzymane; "
                    "ustaw stan ręcznie z faktury i sprawdź połączenie z portalem",
                    gap,
                )
        if changed:
            await self._save()
        return changed

    async def async_set_zone(self, zone: int, value: float, day: date | None = None, append: bool = False) -> None:
        """Ustaw magazyn strefy (kWh) jako partię z datą wprowadzenia ``day`` (domyślnie koniec ostatniego okresu)."""
        if zone not in ZONES:
            raise ValueError(f"Nieznana strefa: {zone}")
        lots = [] if not append else self.state.lots[zone]
        if value > 0:
            lots = [*lots, Lot(day or self.default_lot_date(), round(float(value), 5))]
        self.state.lots[zone] = lots
        self.changed_at = dt_util.utcnow()
        await self._save()

    async def async_reset(self, zone: int | None = None) -> None:
        """Wyzeruj magazyn strefy (albo obu stref, gdy ``zone`` jest None)."""
        for z in ZONES if zone is None else (zone,):
            self.state.lots[z] = []
        self.changed_at = dt_util.utcnow()
        await self._save()
