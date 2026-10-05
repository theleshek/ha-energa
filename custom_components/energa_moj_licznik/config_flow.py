"""Konfiguracja: login/hasło -> wybór PPE."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import EnergaAuthError, EnergaClient, EnergaError, Meter
from .const import (
    CONF_METERS,
    CONF_METERS_INFO,
    CONF_NAMES,
    CONF_STORAGE_RATIO,
    CONF_STORAGE_RATIOS,
    DEFAULT_STORAGE_RATIO,
    DOMAIN,
    LEGACY_STORAGE_RATIO,
    STORAGE_RATIO_CHOICES,
)


_LOGGER = logging.getLogger(__name__)


class EnergaConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._creds: dict[str, str] = {}
        self._meters: list[Meter] = []
        self._selected: list[str] = []
        self._names: dict[str, str] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client = EnergaClient(
                async_create_clientsession(self.hass, cookie_jar=aiohttp.CookieJar()),
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            )
            try:
                await client.async_login()
                self._meters = await client.async_get_meters()
            except EnergaAuthError:
                errors["base"] = "invalid_auth"
            except EnergaError as err:
                _LOGGER.warning("Błąd połączenia z portalem ML: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Nieoczekiwany błąd w config flow")
                errors["base"] = "unknown"
            else:
                if not self._meters:
                    errors["base"] = "no_meters"
                else:
                    self._creds = user_input
                    await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
                    self._abort_if_unique_id_configured()
                    return await self.async_step_meters()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Portal odrzucił zapisane hasło - poproś o nowe."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            client = EnergaClient(
                async_create_clientsession(self.hass, cookie_jar=aiohttp.CookieJar()),
                entry.data[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            )
            try:
                await client.async_login()
            except EnergaAuthError:
                errors["base"] = "invalid_auth"
            except EnergaError as err:
                _LOGGER.warning("Błąd połączenia z portalem ML: %s", err)
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return EnergaOptionsFlow()

    async def async_step_meters(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._selected = user_input[CONF_METERS]
            return await self.async_step_names()
        options = {m.id: _label(m) for m in self._meters}
        return self.async_show_form(
            step_id="meters",
            data_schema=vol.Schema(
                {vol.Required(CONF_METERS, default=list(options)): cv.multi_select(options)}
            ),
        )

    async def async_step_names(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Opcjonalne nazwy własne dla wybranych liczników."""
        chosen = [m for m in self._meters if m.id in self._selected]
        labels = {_label(m): m for m in chosen}
        if user_input is not None:
            names = {
                m.id: user_input.get(label, "").strip()
                for label, m in labels.items()
                if user_input.get(label, "").strip()
            }
            self._names = names
            if any(m.prosumer for m in chosen):
                return await self.async_step_storage()
            return self._create_entry({})
        return self.async_show_form(
            step_id="names",
            data_schema=vol.Schema(
                {vol.Optional(label, default=m.name): str for label, m in labels.items()}
            ),
        )

    async def async_step_storage(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Próg zwrotu z magazynu energii (net metering) dla każdego prosumenta."""
        prosumers = {_label(m): m for m in self._meters if m.id in self._selected and m.prosumer}
        if user_input is not None:
            ratios = {m.id: float(user_input[label]) for label, m in prosumers.items()}
            return self._create_entry({CONF_STORAGE_RATIOS: ratios})
        default = _ratio_key(DEFAULT_STORAGE_RATIO)
        return self.async_show_form(
            step_id="storage",
            data_schema=vol.Schema(
                {vol.Required(label, default=default): vol.In(_ratio_choices()) for label in prosumers}
            ),
        )

    def _create_entry(self, options: dict[str, Any]) -> ConfigFlowResult:
        chosen = [m for m in self._meters if m.id in self._selected]
        return self.async_create_entry(
            title=self._creds[CONF_USERNAME],
            data={
                **self._creds,
                CONF_METERS: self._selected,
                CONF_NAMES: self._names,
                CONF_METERS_INFO: {m.id: {"name": m.name, "ppe": m.ppe} for m in chosen},
            },
            options=options,
        )


def _label(meter: Meter) -> str:
    return f"{meter.name} ({meter.ppe})"


def _ratio_key(ratio: float) -> str:
    return f"{ratio:g}"


def _ratio_choices(current: float | None = None) -> dict[str, str]:
    """Dozwolone progi zwrotu (70 %, 80 %); wartość spoza listy (stara opcja) zostaje dostępna."""
    values = list(STORAGE_RATIO_CHOICES)
    if current is not None and current not in values:
        values.append(current)
    return {_ratio_key(v): f"{_ratio_key(v)} %" for v in sorted(values)}


class EnergaOptionsFlow(OptionsFlow):
    """Zmiana nazw własnych i progu zwrotu z magazynu energii po dodaniu integracji."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        info = entry.data.get(CONF_METERS_INFO, {})
        current = {**entry.data.get(CONF_NAMES, {}), **entry.options.get(CONF_NAMES, {})}
        ids = entry.data.get(CONF_METERS, [])

        def label(mid: str) -> str:
            meta = info.get(mid, {})
            return f"{meta.get('name', mid)} ({meta.get('ppe', mid)})"

        labels = {label(mid): mid for mid in ids}
        # Próg zwrotu dotyczy tylko prosumentów (mają magazyn w koordynatorze); klucze pól odróżniamy od nazw.
        coordinator = self.hass.data.get(DOMAIN, {}).get(entry.entry_id)
        prosumers = set(coordinator.storages) if coordinator else set()
        legacy = float(entry.options.get(CONF_STORAGE_RATIO, LEGACY_STORAGE_RATIO))
        per_meter = entry.options.get(CONF_STORAGE_RATIOS, {})
        current_ratio = {mid: float(per_meter.get(mid, legacy)) for mid in prosumers}
        ratio_labels = {f"{lbl}: zwrot z magazynu": mid for lbl, mid in labels.items() if mid in prosumers}
        if user_input is not None:
            names = {
                mid: user_input.get(lbl, "").strip()
                for lbl, mid in labels.items()
                if user_input.get(lbl, "").strip()
            }
            ratios = {mid: float(user_input[lbl]) for lbl, mid in ratio_labels.items()}
            return self.async_create_entry(data={CONF_NAMES: names, CONF_STORAGE_RATIOS: ratios})
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    **{
                        vol.Optional(lbl, default=current.get(mid) or info.get(mid, {}).get("name", "")): str
                        for lbl, mid in labels.items()
                    },
                    **{
                        vol.Required(lbl, default=_ratio_key(current_ratio[mid])): vol.In(
                            _ratio_choices(current_ratio[mid])
                        )
                        for lbl, mid in ratio_labels.items()
                    },
                }
            ),
        )
