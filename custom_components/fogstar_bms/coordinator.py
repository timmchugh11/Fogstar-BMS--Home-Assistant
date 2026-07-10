from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

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
    CONNECTION_BANK,
    CONNECTION_BLE,
    CONNECTION_SERIAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .jbd import FogstarBmsData, FogstarBmsError, read_bms

LOGGER = logging.getLogger(__name__)


class FogstarBmsCoordinator(DataUpdateCoordinator[FogstarBmsData]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.port = entry.data.get(CONF_PORT)
        self.baudrate = entry.data.get(CONF_BAUDRATE)
        interval = entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

        super().__init__(
            hass,
            LOGGER,
            name=f"{DOMAIN}_{entry.data[CONF_NAME]}",
            update_interval=timedelta(seconds=interval),
        )

    async def _async_update_data(self) -> FogstarBmsData:
        try:
            connection_type = self.entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_SERIAL)
            if connection_type == CONNECTION_BANK:
                return self._read_battery_bank()
            if connection_type == CONNECTION_BLE:
                return await read_bms_ble(
                    self.entry.data.get(CONF_BLE_ADDRESS) or None,
                    self.entry.data.get(CONF_BLE_NAME) or None,
                    self.entry.data.get(CONF_PASSWORD, ""),
                    False,
                    self.hass,
                )
            return await self.hass.async_add_executor_job(
                read_bms, self.port, self.baudrate
            )
        except (FogstarBmsError, OSError, TimeoutError) as err:
            raise UpdateFailed(str(err)) from err

    def _read_battery_bank(self) -> FogstarBmsData:
        entry_ids = self.entry.data[CONF_BANK_ENTRIES]
        coordinators = []
        for entry_id in entry_ids:
            coordinator = self.hass.data.get(DOMAIN, {}).get(entry_id)
            if coordinator is None or coordinator.data is None:
                raise UpdateFailed(f"Battery entry {entry_id} is not available")
            coordinators.append(coordinator)

        batteries = [coordinator.data for coordinator in coordinators]
        if not batteries:
            raise UpdateFailed("No batteries selected")

        nominal_capacity = sum(battery.nominal_capacity for battery in batteries)
        remaining_capacity = sum(battery.remaining_capacity for battery in batteries)
        if nominal_capacity:
            state_of_charge = round((remaining_capacity / nominal_capacity) * 100.0, 2)
        else:
            state_of_charge = round(
                sum(battery.state_of_charge for battery in batteries) / len(batteries),
                2,
            )

        cells = [cell for battery in batteries for cell in battery.cells]
        temperatures = [
            temperature
            for battery in batteries
            for temperature in battery.temperatures
        ]

        return FogstarBmsData(
            hardware="Battery bank",
            voltage=round(sum(battery.voltage for battery in batteries) / len(batteries), 2),
            current=round(sum(battery.current for battery in batteries), 2),
            state_of_charge=state_of_charge,
            remaining_capacity=round(remaining_capacity, 2),
            nominal_capacity=round(nominal_capacity, 2),
            cycles=max(battery.cycles for battery in batteries),
            production_date="",
            software_version=0,
            fet_state=_combine_fet_state([battery.fet_state for battery in batteries]),
            protection_flags=_combine_protection_flags(
                [battery.protection_flags for battery in batteries]
            ),
            balance_flags_low=0,
            balance_flags_high=0,
            cells=cells,
            temperatures=temperatures,
        )


def _combine_fet_state(values: list[int]) -> int:
    if not values:
        return 0
    charge_enabled = all(value & 0x01 for value in values)
    discharge_enabled = all(value & 0x02 for value in values)
    return (0x01 if charge_enabled else 0) | (0x02 if discharge_enabled else 0)


def _combine_protection_flags(values: list[int]) -> int:
    combined = 0
    for value in values:
        combined |= value
    return combined
