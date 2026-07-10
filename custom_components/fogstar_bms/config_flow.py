from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .ble import read_bms_ble
from .const import (
    CONF_BAUDRATE,
    CONF_BLE_ADDRESS,
    CONF_BLE_NAME,
    CONF_CONNECTION_TYPE,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONNECTION_BLE,
    CONNECTION_SERIAL,
    DEFAULT_BAUDRATE,
    DEFAULT_BLE_NAME,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from .jbd import FogstarBmsError, read_bms

LOGGER = logging.getLogger(__name__)


class FogstarBmsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            connection_type = user_input[CONF_CONNECTION_TYPE]
            if connection_type == CONNECTION_BLE:
                return await self.async_step_bluetooth()
            return await self.async_step_serial()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONNECTION_TYPE, default=CONNECTION_BLE): vol.In(
                        {
                            CONNECTION_BLE: "Bluetooth",
                            CONNECTION_SERIAL: "Serial / RS485",
                        }
                    )
                }
            ),
        )

    async def async_step_serial(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            port = user_input[CONF_PORT]
            baudrate = user_input[CONF_BAUDRATE]
            await self.async_set_unique_id(_serial_unique_id(port, baudrate))
            self._abort_if_unique_id_configured()

            try:
                await self.hass.async_add_executor_job(read_bms, port, baudrate)
            except (FogstarBmsError, OSError, TimeoutError) as err:
                LOGGER.warning("Serial BMS setup failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                data = {**user_input, CONF_CONNECTION_TYPE: CONNECTION_SERIAL}
                return self.async_create_entry(title=user_input[CONF_NAME], data=data)

        return self.async_show_form(
            step_id="serial",
            data_schema=_serial_schema(user_input),
            errors=errors,
        )

    async def async_step_bluetooth(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input.get(CONF_BLE_ADDRESS, "").strip()
            ble_name = user_input.get(CONF_BLE_NAME, "").strip()
            await self.async_set_unique_id(_ble_unique_id(address, ble_name))
            self._abort_if_unique_id_configured()

            try:
                await read_bms_ble(
                    address or None,
                    ble_name or None,
                    user_input.get(CONF_PASSWORD, ""),
                    False,
                )
            except (FogstarBmsError, OSError, TimeoutError) as err:
                LOGGER.warning("Bluetooth BMS setup failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                data = {
                    **user_input,
                    CONF_BLE_ADDRESS: address,
                    CONF_BLE_NAME: ble_name,
                    CONF_CONNECTION_TYPE: CONNECTION_BLE,
                }
                return self.async_create_entry(title=user_input[CONF_NAME], data=data)

        return self.async_show_form(
            step_id="bluetooth",
            data_schema=_bluetooth_schema(user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return FogstarBmsOptionsFlow(config_entry)


class FogstarBmsOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={**self.entry.data, **user_input},
            )
            return self.async_create_entry(title="", data={})

        connection_type = self.entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_SERIAL)
        schema = (
            _bluetooth_schema(self.entry.data, include_name=False)
            if connection_type == CONNECTION_BLE
            else _serial_schema(self.entry.data, include_name=False)
        )
        return self.async_show_form(step_id="init", data_schema=schema)


def _common_fields(defaults: dict[str, Any] | None, include_name: bool) -> dict[Any, Any]:
    defaults = defaults or {}
    fields: dict[Any, Any] = {}
    if include_name:
        fields[vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME))] = str
    fields[
        vol.Required(
            CONF_SCAN_INTERVAL,
            default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
    ] = vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL))
    return fields


def _serial_schema(defaults: dict[str, Any] | None, include_name: bool = True) -> vol.Schema:
    defaults = defaults or {}
    fields = _common_fields(defaults, include_name)
    fields[vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT))] = str
    fields[
        vol.Required(CONF_BAUDRATE, default=defaults.get(CONF_BAUDRATE, DEFAULT_BAUDRATE))
    ] = int
    return vol.Schema(fields)


def _bluetooth_schema(defaults: dict[str, Any] | None, include_name: bool = True) -> vol.Schema:
    defaults = defaults or {}
    fields = _common_fields(defaults, include_name)
    fields[
        vol.Optional(CONF_BLE_ADDRESS, default=defaults.get(CONF_BLE_ADDRESS, ""))
    ] = str
    fields[
        vol.Required(CONF_BLE_NAME, default=defaults.get(CONF_BLE_NAME, DEFAULT_BLE_NAME))
    ] = str
    fields[vol.Required(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, ""))] = str
    return vol.Schema(fields)


def _serial_unique_id(port: str, baudrate: int) -> str:
    return f"serial-{port.strip().casefold()}-{baudrate}"


def _ble_unique_id(address: str, name: str) -> str:
    if address:
        return f"ble-{address.strip().casefold()}"
    return f"ble-name-{name.strip().casefold()}"
