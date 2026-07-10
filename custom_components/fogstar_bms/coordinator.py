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
    CONF_BLE_ADDRESS,
    CONF_BLE_NAME,
    CONF_CONNECTION_TYPE,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
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
            if connection_type == CONNECTION_BLE:
                return await read_bms_ble(
                    self.entry.data.get(CONF_BLE_ADDRESS) or None,
                    self.entry.data.get(CONF_BLE_NAME) or None,
                    self.entry.data.get(CONF_PASSWORD, ""),
                    False,
                )
            return await self.hass.async_add_executor_job(
                read_bms, self.port, self.baudrate
            )
        except (FogstarBmsError, OSError, TimeoutError) as err:
            raise UpdateFailed(str(err)) from err
