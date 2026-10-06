"""Sensory: stany licznika (A+/A-, strefy 1 i 2) oraz zużycie dzienne."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
import voluptuous as vol

from homeassistant.const import UnitOfEnergy
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ZONES
from .storage import ZONES as STORAGE_ZONES

NAMES = {
    "A+1": "Pobór strefa 1",
    "A+2": "Pobór strefa 2",
    "A-1": "Oddanie strefa 1",
    "A-2": "Oddanie strefa 2",
}

BALANCE_NAMES = {
    "1": "Bilans strefa 1",
    "2": "Bilans strefa 2",
    "all": "Bilans łącznie",
}

DAILY_NAMES = {
    ("A+", "today"): "Pobór dziś",
    ("A+", "yesterday"): "Pobór wczoraj",
    ("A-", "today"): "Oddanie dziś",
    ("A-", "yesterday"): "Oddanie wczoraj",
    ("BAL", "today"): "Bilans dziś",
    ("BAL", "yesterday"): "Bilans wczoraj",
}

MONTHLY_NAMES = {
    "this": "Bilans ten miesiąc",
    "previous": "Bilans poprzedni miesiąc",
}


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        EnergaReadingSensor(coordinator, meter, zone)
        for meter in coordinator.meters
        for zone in ZONES
        # Konto bez oddawania (nie-prosument): pomijamy A-
        if zone.startswith("A+") or meter.prosumer
    ]
    entities += [
        EnergaBalanceSensor(coordinator, meter, key)
        for meter in coordinator.meters
        if meter.prosumer
        for key in BALANCE_NAMES
    ]
    for meter in coordinator.meters:
        for (direction, day) in DAILY_NAMES:
            if direction != "A+" and not meter.prosumer:
                continue
            entities.append(EnergaDailySensor(coordinator, meter, direction, day))
    entities += [
        EnergaMonthlySensor(coordinator, meter, period)
        for meter in coordinator.meters
        if meter.prosumer
        for period in MONTHLY_NAMES
    ]
    entities += [
        EnergaStorageSensor(coordinator, meter, zone)
        for meter in coordinator.meters
        if meter.id in coordinator.storages
        for zone in (*STORAGE_ZONES, None)
    ]
    async_add_entities(entities)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "set_storage",
        {
            vol.Required("value"): vol.All(vol.Coerce(float), vol.Range(min=0)),
            vol.Optional("date"): cv.date,
            vol.Optional("append", default=False): cv.boolean,
        },
        "async_set_storage",
    )
    platform.async_register_entity_service("reset_storage", {}, "async_reset_storage")


def _device_info(meter) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, meter.ppe)}, name=meter.name, manufacturer="Energa-Operator")


class EnergaReadingSensor(CoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 4
    _attr_has_entity_name = True

    def __init__(self, coordinator, meter, zone) -> None:
        super().__init__(coordinator)
        self._meter = meter
        self._zone = zone
        self._attr_unique_id = f"{meter.ppe}_{zone}"
        self._attr_name = NAMES[zone]
        self._attr_device_info = _device_info(meter)

    def _readings(self):
        data = self.coordinator.data.get(self._meter.id)
        return data.readings if data else None

    @property
    def native_value(self):
        readings = self._readings()
        value = readings.values.get(self._zone) if readings else None
        return float(value) if value is not None else None

    @property
    def extra_state_attributes(self):
        readings = self._readings()
        attrs = {}
        if readings and readings.timestamp:
            attrs["reading_time"] = readings.timestamp.isoformat()
        if self.coordinator.last_refresh:
            attrs["last_refresh"] = self.coordinator.last_refresh.isoformat()
        return attrs


class EnergaBalanceSensor(CoordinatorEntity, SensorEntity):
    """Bilans stanu liczników: pobór (A+) minus oddanie (A-), per strefa i łącznie.

    Bez state_class (wartość może maleć); nie dodawaj do panelu Energia.
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 4
    _attr_has_entity_name = True

    def __init__(self, coordinator, meter, key: str) -> None:
        super().__init__(coordinator)
        self._meter = meter
        self._key = key  # '1', '2' albo 'all'
        self._attr_unique_id = f"{meter.ppe}_balance_{key}"
        self._attr_name = BALANCE_NAMES[key]
        self._attr_device_info = _device_info(meter)

    @property
    def native_value(self):
        data = self.coordinator.data.get(self._meter.id)
        values = data.readings.values if data and data.readings else {}
        zones = ("1", "2") if self._key == "all" else (self._key,)
        total, found = 0.0, False
        for z in zones:
            plus, minus = values.get(f"A+{z}"), values.get(f"A-{z}")
            if plus is None or minus is None:
                continue
            total += float(plus) - float(minus)
            found = True
        return round(total, 4) if found else None

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data.get(self._meter.id)
        if data and data.readings and data.readings.timestamp:
            return {"reading_time": data.readings.timestamp.isoformat()}
        return {}


class EnergaDailySensor(CoordinatorEntity, SensorEntity):
    """Zużycie doby (dziś/wczoraj) policzone z kompletnych godzin z portalu.

    Bez state_class: do panelu Energia służą statystyki godzinowe
    (energa_moj_licznik:<PPE>_pobor itd.), a te sensory są do wyświetlania i automatyzacji.
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3
    _attr_has_entity_name = True

    def __init__(self, coordinator, meter, direction: str, day: str) -> None:
        super().__init__(coordinator)
        self._meter = meter
        self._direction = direction  # 'A+', 'A-' albo 'BAL' (pobór minus oddanie)
        self._day = day
        self._attr_unique_id = f"{meter.ppe}_daily_{direction}_{day}".replace("+", "plus").replace("-", "minus")
        self._attr_name = DAILY_NAMES[(direction, day)]
        self._attr_device_info = _device_info(meter)

    def _usage(self, direction: str):
        data = self.coordinator.data.get(self._meter.id)
        return (data.daily.get(direction) or {}).get(self._day) if data else None

    @property
    def native_value(self):
        if self._direction == "BAL":
            plus, minus = self._usage("A+"), self._usage("A-")
            return round(plus.total - minus.total, 5) if plus and minus else None
        usage = self._usage(self._direction)
        return usage.total if usage else None

    @property
    def extra_state_attributes(self):
        usage = self._usage("A+" if self._direction == "BAL" else self._direction)
        if not usage:
            return {}
        attrs = {"date": usage.day.isoformat(), "complete_hours": usage.hours}
        if self._direction != "BAL":
            attrs.update({f"zone_{z}": v for z, v in usage.zones.items()})
        return attrs


class EnergaMonthlySensor(CoordinatorEntity, SensorEntity):
    """Bilans miesiąca (pobór - oddanie) z wykresu rocznego portalu (mo=BP).

    Wartość ujemna = więcej oddano niż pobrano. Bieżący miesiąc zmienia się z dnia na dzień.
    Bez state_class: nie dodawaj do panelu Energia.
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3
    _attr_has_entity_name = True

    def __init__(self, coordinator, meter, period: str) -> None:
        super().__init__(coordinator)
        self._meter = meter
        self._period = period  # 'this' albo 'previous'
        self._attr_unique_id = f"{meter.ppe}_monthly_balance_{period}"
        self._attr_name = MONTHLY_NAMES[period]
        self._attr_device_info = _device_info(meter)

    def _balance(self):
        data = self.coordinator.data.get(self._meter.id)
        return data.monthly.get(self._period) if data else None

    @property
    def native_value(self):
        balance = self._balance()
        return balance.value if balance else None

    @property
    def extra_state_attributes(self):
        balance = self._balance()
        if not balance:
            return {}
        return {"month": balance.month.strftime("%Y-%m"), "complete": balance.complete}


class EnergaStorageSensor(CoordinatorEntity, SensorEntity):
    """Magazyn energii u operatora (net metering) w strefie 1, strefie 2 albo razem (zone=None).

    Wartość to stan, jaki wyniknąłby z rozliczenia bieżącego okresu rozliczeniowego "teraz"
    (godzinowe salda per strefa, współczynnik 0,7/0,8 na nadwyżkę, pobór najpierw z tej samej strefy,
    partie FIFO ważne 12 miesięcy) - tak jak liczy Energa na fakturze. Stan początkowy ustaw z faktury
    usługą set_storage lub encją number. Bez state_class: nie dodawaj do panelu Energia.
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3
    _attr_has_entity_name = True
    _attr_icon = "mdi:battery-charging-medium"

    def __init__(self, coordinator, meter, zone: int | None) -> None:
        super().__init__(coordinator)
        self._meter = meter
        self._zone = zone  # 1, 2 albo None = razem
        self._store = coordinator.storages[meter.id]
        self._attr_unique_id = f"{meter.ppe}_storage_total" if zone is None else f"{meter.ppe}_storage_zone_{zone}"
        self._attr_name = "Magazyn energii razem" if zone is None else f"Magazyn energii strefa {zone}"
        self._attr_device_info = _device_info(meter)

    def _zones(self):
        return STORAGE_ZONES if self._zone is None else (self._zone,)

    @property
    def native_value(self):
        lots, _ = self._store.projection()
        return round(sum(lot.amount for z in self._zones() for lot in lots[z]), 3)

    @property
    def extra_state_attributes(self):
        store, state = self._store, self._store.state
        lots, billed = store.projection()
        start, end = store.period()
        attrs = {
            "lots": [
                {"zone": z, "date": lot.day.isoformat(), "kwh": round(lot.amount, 3)}
                for z in self._zones()
                for lot in lots[z]
            ],
            "settled_kwh": round(sum(lot.amount for z in self._zones() for lot in state.lots[z]), 3),
            "to_pay_kwh": round(sum(billed[z] for z in self._zones()), 3),
            "positive_balance_kwh": round(sum(state.pos[z] for z in self._zones()), 3),
            "negative_balance_kwh": round(sum(state.neg[z] for z in self._zones()), 3),
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "period_months": store.months,
            "ratio_percent": round(store.ratio * 100, 1),
        }
        if state.last_hour:
            attrs["calculated_until"] = state.last_hour.isoformat()
        if store.gap_from:
            attrs["data_gap_from"] = store.gap_from.isoformat()
        if store.changed_at:
            attrs["last_set"] = store.changed_at.isoformat()
        return attrs

    async def async_set_storage(self, value: float, date=None, append: bool = False) -> None:
        if self._zone is None:
            raise ServiceValidationError("Stan ustawia się osobno dla strefy 1 i strefy 2 (wybierz encję strefy).")
        await self._store.async_set_zone(self._zone, value, date, append)
        self.coordinator.async_update_listeners()

    async def async_reset_storage(self) -> None:
        await self._store.async_reset(self._zone)
        self.coordinator.async_update_listeners()
