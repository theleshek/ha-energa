"""Integracja Energa Mój Licznik."""
from __future__ import annotations

from dataclasses import replace
import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import EnergaAuthError, EnergaClient, EnergaError
from .const import (
    CONF_METERS,
    CONF_NAMES,
    CONF_STORAGE_RATIO,
    CONF_STORAGE_PERIOD,
    CONF_STORAGE_RATIOS,
    DEFAULT_STORAGE_PERIOD,
    DOMAIN,
    LEGACY_STORAGE_RATIO,
)
from .coordinator import EnergaCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.NUMBER]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Osobna sesja: portal korzysta z ciasteczek, nie mieszamy ich z globalną sesją HA.
    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
    client = EnergaClient(session, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    try:
        await client.async_login()
        all_meters = await client.async_get_meters()
    except EnergaAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except EnergaError as err:
        raise ConfigEntryNotReady(str(err)) from err
    selected = set(entry.data[CONF_METERS])
    # Nazwy własne (opcje mają pierwszeństwo przed danymi z konfiguracji)
    names = {**entry.data.get(CONF_NAMES, {}), **entry.options.get(CONF_NAMES, {})}
    meters = [
        replace(m, name=(names.get(m.id) or "").strip() or m.name)
        for m in all_meters
        if m.id in selected
    ]
    missing = selected - {m.id for m in meters}
    if missing:
        _LOGGER.warning("Wybrane liczniki nie są już dostępne na koncie: %s", sorted(missing))
    _LOGGER.info(
        "Energa Mój Licznik: konto %s, liczniki: %s",
        entry.data[CONF_USERNAME],
        ", ".join(f"{m.name} ({m.tariff}, {'prosument' if m.prosumer else 'odbiorca'})" for m in meters) or "brak",
    )
    # Próg zwrotu z magazynu per licznik; wpisy sprzed tej opcji zachowują dotychczasową wartość wspólną.
    legacy = float(entry.options.get(CONF_STORAGE_RATIO, LEGACY_STORAGE_RATIO))
    per_meter = entry.options.get(CONF_STORAGE_RATIOS, {})
    ratios = {m.id: float(per_meter.get(m.id, legacy)) for m in meters}
    period = int(entry.options.get(CONF_STORAGE_PERIOD, DEFAULT_STORAGE_PERIOD))
    coordinator = EnergaCoordinator(hass, client, meters, entry.entry_id, ratios, period)
    await coordinator.async_load_storages()
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_update))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded


async def _async_reload_on_update(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Przeładuj integrację po zmianie opcji (np. nazw liczników)."""
    await hass.config_entries.async_reload(entry.entry_id)
