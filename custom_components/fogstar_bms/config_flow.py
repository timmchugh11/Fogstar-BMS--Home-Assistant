from __future__ import annotations

import logging
from typing import Any

from bleak import BleakScanner
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import bluetooth
import homeassistant.helpers.config_validation as cv
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .ble import read_bms_ble
from .const import (
    CONF_BAUDRATE,
    CONF_BANK_ENTRIES,
    CONF_BLE_ADDRESS,
    CONF_BLE_NAME,
    CONF_CONNECTION_TYPE,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONNECTION_BLE,
    CONNECTION_BANK,
    CONNECTION_SERIAL,
    DEFAULT_BAUDRATE,
    DEFAULT_BLE_NAME,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_BLE_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .jbd import FogstarBmsError, read_bms

LOGGER = logging.getLogger(__name__)
CONF_BLE_DEVICE = "ble_device"
MANUAL_BLE_DEVICE = "manual"


class FogstarBmsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._ble_devices: dict[str, str] = {}
        self._ble_defaults: dict[str, Any] | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            connection_type = user_input[CONF_CONNECTION_TYPE]
            if connection_type == CONNECTION_BLE:
                return await self.async_step_bluetooth()
            if connection_type == CONNECTION_BANK:
                return await self.async_step_battery_bank()
            return await self.async_step_serial()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONNECTION_TYPE, default=CONNECTION_BLE): vol.In(
                        {
                            CONNECTION_BLE: "Bluetooth",
                            CONNECTION_BANK: "Battery bank",
                            CONNECTION_SERIAL: "Serial / RS485",
                        }
                    )
                }
            ),
        )

    async def _async_discover_ble_devices(self) -> dict[str, str]:
        choices: dict[str, str] = {}
        for service_info in bluetooth.async_discovered_service_info(
            self.hass,
            connectable=True,
        ):
            if not service_info.address:
                continue
            label = f"{service_info.name or 'Unknown'} ({service_info.address})"
            choices[service_info.address] = label

        devices = await BleakScanner.discover(timeout=8, return_adv=False)
        for device in devices:
            if not device.address:
                continue
            if device.address in choices:
                continue
            label = f"{device.name or 'Unknown'} ({device.address})"
            choices[device.address] = label
        return dict(sorted(choices.items(), key=lambda item: item[1].casefold()))

    async def async_step_bluetooth(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            selected = user_input[CONF_BLE_DEVICE]
            if selected == MANUAL_BLE_DEVICE:
                self._ble_defaults = None
                return await self.async_step_bluetooth_manual()
            name = self._ble_devices.get(selected, selected)
            name = name.rsplit(" (", 1)[0]
            self._ble_defaults = {
                CONF_NAME: name if name != "Unknown" else DEFAULT_NAME,
                CONF_BLE_ADDRESS: selected,
                CONF_BLE_NAME: name if name != "Unknown" else DEFAULT_BLE_NAME,
                CONF_PASSWORD: "",
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
            }
            return await self.async_step_bluetooth_manual()

        try:
            self._ble_devices = await self._async_discover_ble_devices()
        except Exception as err:
            LOGGER.warning("Bluetooth discovery failed: %s", err)
            errors["base"] = "cannot_connect"
            self._ble_devices = {}

        choices = {**self._ble_devices, MANUAL_BLE_DEVICE: "Enter address manually"}
        return self.async_show_form(
            step_id="bluetooth",
            data_schema=vol.Schema({vol.Required(CONF_BLE_DEVICE): vol.In(choices)}),
            errors=errors,
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

    async def async_step_bluetooth_manual(self, user_input: dict[str, Any] | None = None) -> FlowResult:
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
                    self.hass,
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
            step_id="bluetooth_manual",
            data_schema=_bluetooth_schema(user_input or self._ble_defaults),
            errors=errors,
        )

    async def async_step_battery_bank(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        entries = {
            entry.entry_id: entry.title
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.data.get(CONF_CONNECTION_TYPE) != CONNECTION_BANK
        }
        if user_input is not None:
            selected_entries = list(user_input[CONF_BANK_ENTRIES])
            if len(selected_entries) < 2:
                errors["base"] = "not_enough_batteries"
            else:
                await self.async_set_unique_id(_bank_unique_id(selected_entries))
                self._abort_if_unique_id_configured()
                data = {
                    **user_input,
                    CONF_BANK_ENTRIES: selected_entries,
                    CONF_CONNECTION_TYPE: CONNECTION_BANK,
                }
                return self.async_create_entry(title=user_input[CONF_NAME], data=data)

        return self.async_show_form(
            step_id="battery_bank",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                    vol.Required(CONF_BANK_ENTRIES): cv.multi_select(entries),
                    vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
                        vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL)
                    ),
                }
            ),
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
        if connection_type == CONNECTION_BANK:
            schema = _bank_options_schema(self.entry.data)
        elif connection_type == CONNECTION_BLE:
            schema = _bluetooth_schema(self.entry.data, include_name=False)
        else:
            schema = _serial_schema(self.entry.data, include_name=False)
        return self.async_show_form(step_id="init", data_schema=schema)


def _common_fields(
    defaults: dict[str, Any] | None,
    include_name: bool,
    min_scan_interval: int = MIN_SCAN_INTERVAL,
) -> dict[Any, Any]:
    defaults = defaults or {}
    fields: dict[Any, Any] = {}
    if include_name:
        fields[vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME))] = str
    fields[
        vol.Required(
            CONF_SCAN_INTERVAL,
            default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
    ] = vol.All(vol.Coerce(int), vol.Range(min=min_scan_interval))
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
    fields = _common_fields(defaults, include_name, MIN_BLE_SCAN_INTERVAL)
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


def _bank_unique_id(entry_ids: list[str]) -> str:
    return "bank-" + "-".join(sorted(entry_ids))


def _bank_options_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL))
        }
    )
