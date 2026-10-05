"""Sensory: stany licznika (A+/A-, strefy 1 i 2) oraz zużycie dzienne."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
import voluptuous as vol

from homeassistant.const import UnitOfEnergy
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ZONES

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
        EnergaStorageSensor(coordinator, meter) for meter in coordinator.meters if meter.id in coordinator.storages
    ]
    async_add_entities(entities)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "set_storage", {vol.Required("value"): vol.All(vol.Coerce(float), vol.Range(min=0))}, "async_set_storage"
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


class EnergaStorageSensor(CoordinatorEntity, SensorEntity):
    """Stan magazynu energii u operatora (system opustów).

    Oddanie zasila magazyn w części (opcja "storage_ratios", per licznik: 70 % lub 80 %), pobór go
    opróżnia. Wartość liczona godzinowo z danych portalu i zapamiętywana; ustaw ją usługą
    set_storage (albo encją number) przy pierwszym uruchomieniu lub po rozliczeniu z operatorem.
    Bez state_class: nie dodawaj do panelu Energia.
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3
    _attr_has_entity_name = True
    _attr_name = "Magazyn energii"
    _attr_icon = "mdi:battery-charging-medium"

    def __init__(self, coordinator, meter) -> None:
        super().__init__(coordinator)
        self._meter = meter
        self._store = coordinator.storages[meter.id]
        self._attr_unique_id = f"{meter.ppe}_storage"
        self._attr_device_info = _device_info(meter)

    @property
    def native_value(self):
        return round(self._store.value, 3)

    @property
    def extra_state_attributes(self):
        s = self._store
        attrs = {
            "ratio_percent": round(s.ratio * 100, 1),
            "credited_since_set": s.credited,
            "drawn_since_set": s.drawn,
        }
        if s.last_hour:
            attrs["calculated_until"] = s.last_hour.isoformat()
        if s.changed_at:
            attrs["last_set"] = s.changed_at.isoformat()
        return attrs

    async def async_set_storage(self, value: float) -> None:
        await self._store.async_set(value)
        self.coordinator.async_update_listeners()

    async def async_reset_storage(self) -> None:
        await self.async_set_storage(0.0)
