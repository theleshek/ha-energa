"""Encja number do ręcznego ustawienia stanu magazynu energii u operatora."""
from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfEnergy
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .sensor import _device_info


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        EnergaStorageNumber(coordinator, meter) for meter in coordinator.meters if meter.id in coordinator.storages
    )


class EnergaStorageNumber(CoordinatorEntity, NumberEntity):
    """Ustaw stan magazynu (kWh); sensor "Magazyn energii" pokazuje wartość bieżącą."""

    _attr_device_class = NumberDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 100000
    _attr_native_step = 0.001
    _attr_has_entity_name = True
    _attr_name = "Ustaw magazyn energii"
    _attr_icon = "mdi:battery-edit"

    def __init__(self, coordinator, meter) -> None:
        super().__init__(coordinator)
        self._store = coordinator.storages[meter.id]
        self._attr_unique_id = f"{meter.ppe}_storage_set"
        self._attr_device_info = _device_info(meter)

    @property
    def native_value(self):
        return round(self._store.value, 3)

    async def async_set_native_value(self, value: float) -> None:
        await self._store.async_set(value)
        self.coordinator.async_update_listeners()
