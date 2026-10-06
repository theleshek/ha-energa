"""Encje number do ręcznego ustawienia stanu magazynu energii u operatora (osobno strefa 1 i 2)."""
from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfEnergy
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .sensor import _device_info
from .storage import ZONES


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        EnergaStorageNumber(coordinator, meter, zone)
        for meter in coordinator.meters
        if meter.id in coordinator.storages
        for zone in ZONES
    )


class EnergaStorageNumber(CoordinatorEntity, NumberEntity):
    """Ustaw magazyn strefy (kWh) jako jedną partię z końca ostatniego okresu rozliczeniowego.

    Wiele partii z różnymi datami (ważność 12 miesięcy) ustawisz usługą set_storage (pole date, append).
    """

    _attr_device_class = NumberDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 100000
    _attr_native_step = 0.001
    _attr_has_entity_name = True
    _attr_icon = "mdi:battery-edit"

    def __init__(self, coordinator, meter, zone: int) -> None:
        super().__init__(coordinator)
        self._zone = zone
        self._store = coordinator.storages[meter.id]
        self._attr_unique_id = f"{meter.ppe}_storage_set_zone_{zone}"
        self._attr_name = f"Ustaw magazyn energii strefa {zone}"
        self._attr_device_info = _device_info(meter)

    @property
    def native_value(self):
        lots, _ = self._store.projection()
        return round(sum(lot.amount for lot in lots[self._zone]), 3)

    async def async_set_native_value(self, value: float) -> None:
        await self._store.async_set_zone(self._zone, value)
        self.coordinator.async_update_listeners()
