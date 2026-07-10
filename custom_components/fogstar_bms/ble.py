from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakConnectionError, establish_connection

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .jbd import FogstarBmsData, FogstarBmsError, REQUESTS, decode_bms_payloads

NOTIFY_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"
ROOT_PASSWORD = bytes.fromhex("4a 42 44 62 74 70 77 64 21 40 23 32 30 32 33")
LOGGER = logging.getLogger(__name__)
_BLE_CONNECTION_LOCK = asyncio.Lock()


def _checksum_ok(frame: bytes) -> bool:
    if len(frame) < 7 or frame[0] != 0xDD or frame[-1] != 0x77:
        return False
    length = frame[3]
    if len(frame) != 4 + length + 3:
        return False
    received = (frame[4 + length] << 8) | frame[5 + length]
    return ((sum(frame[2 : 4 + length]) + received) & 0xFFFF) == 0


def _auth_checksum(data: bytes) -> int:
    return sum(data) & 0xFF


def _auth_frame(command: int, payload: bytes = b"") -> bytes:
    body = bytes((command, len(payload))) + payload
    return b"\xff\xaa" + body + bytes((_auth_checksum(body),))


def _mac_bytes(address: str, reverse: bool = False) -> bytes:
    data = bytes(int(part, 16) for part in address.split(":"))
    return data[::-1] if reverse else data


async def _find_device(
    hass: HomeAssistant | None,
    address: str | None,
    name: str | None,
) -> BLEDevice | None:
    if hass is not None:
        if address:
            device = bluetooth.async_ble_device_from_address(
                hass,
                address,
                connectable=True,
            )
            if device is not None:
                return device

        if name:
            lowered_name = name.lower()
            for service_info in bluetooth.async_discovered_service_info(
                hass,
                connectable=True,
            ):
                if service_info.name and lowered_name in service_info.name.lower():
                    return service_info.device

    if address:
        return await BleakScanner.find_device_by_address(address, timeout=15)
    return await BleakScanner.find_device_by_filter(
        lambda device, adv: bool(device.name and name and name.lower() in device.name.lower()),
        timeout=15,
    )


async def _auth_exchange(
    client: BleakClient,
    frame: bytes,
    expected_command: int,
) -> bytes:
    event = asyncio.Event()
    response = bytearray()

    def on_notify(_sender, data: bytearray) -> None:
        if len(data) < 5 or data[0] != 0xFF or data[1] != 0xAA or data[2] != expected_command:
            return
        length = data[3]
        if len(data) < 5 + length:
            return
        if _auth_checksum(bytes(data[2 : 4 + length])) != data[4 + length]:
            return
        response[:] = data[4 : 4 + length]
        event.set()

    await client.start_notify(NOTIFY_UUID, on_notify)
    try:
        await client.write_gatt_char(WRITE_UUID, frame, response=False)
        await asyncio.wait_for(event.wait(), timeout=5)
    finally:
        await client.stop_notify(NOTIFY_UUID)
    return bytes(response)


async def _authenticate(
    client: BleakClient,
    address: str,
    password: str,
    reverse_mac: bool,
) -> None:
    LOGGER.debug("Starting BLE BMS authentication reverse_mac=%s", reverse_mac)
    mac = _mac_bytes(address, reverse=reverse_mac)
    expected_command: int | None = None
    response = bytearray()
    event = asyncio.Event()

    def on_notify(_sender, data: bytearray) -> None:
        if (
            expected_command is None
            or len(data) < 5
            or data[0] != 0xFF
            or data[1] != 0xAA
            or data[2] != expected_command
        ):
            return
        length = data[3]
        if len(data) < 5 + length:
            return
        if _auth_checksum(bytes(data[2 : 4 + length])) != data[4 + length]:
            return
        response[:] = data[4 : 4 + length]
        event.set()

    async def exchange(frame: bytes, command: int) -> bytes:
        nonlocal expected_command
        response.clear()
        event.clear()
        expected_command = command
        await client.write_gatt_char(WRITE_UUID, frame, response=False)
        await asyncio.wait_for(event.wait(), timeout=5)
        expected_command = None
        return bytes(response)

    await client.start_notify(NOTIFY_UUID, on_notify)
    try:
        await asyncio.sleep(0.5)
        app_key_response = await exchange(_auth_frame(0x15, b"000000"), 0x15)
        LOGGER.debug(
            "BLE BMS app key response reverse_mac=%s: %s",
            reverse_mac,
            app_key_response.hex(" "),
        )
        if app_key_response and app_key_response[0] == 0x02:
            return
        if not app_key_response or app_key_response[0] != 0x00:
            raise FogstarBmsError(f"app key rejected: {app_key_response.hex(' ')}")

        random_byte = (await exchange(_auth_frame(0x17), 0x17))[0]
        encrypted_password = bytes(
            ((mac[index] ^ ord(password[index])) + random_byte) & 0xFF
            for index in range(6)
        )
        password_response = await exchange(_auth_frame(0x18, encrypted_password), 0x18)
        LOGGER.debug(
            "BLE BMS password response reverse_mac=%s: %s",
            reverse_mac,
            password_response.hex(" "),
        )
        if not password_response or password_response[0] != 0x00:
            raise FogstarBmsError(f"password rejected: {password_response.hex(' ')}")

        random_byte = (await exchange(_auth_frame(0x17), 0x17))[0]
        encrypted_root = bytes(
            (((mac[index] if index < 6 else 0x00) ^ ROOT_PASSWORD[index]) + random_byte)
            & 0xFF
            for index in range(len(ROOT_PASSWORD))
        )
        root_response = await exchange(_auth_frame(0x1D, encrypted_root), 0x1D)
        LOGGER.debug(
            "BLE BMS root password response reverse_mac=%s: %s",
            reverse_mac,
            root_response.hex(" "),
        )
        if not root_response or root_response[0] != 0x00:
            raise FogstarBmsError(f"root password rejected: {root_response.hex(' ')}")
    finally:
        await client.stop_notify(NOTIFY_UUID)


async def _query(client: BleakClient, frame: bytes) -> bytes:
    event = asyncio.Event()
    chunks = bytearray()

    def on_notify(_sender, data: bytearray) -> None:
        chunks.extend(data)
        if chunks and chunks[0] != 0xDD:
            start = chunks.find(b"\xdd")
            if start >= 0:
                del chunks[:start]
        if len(chunks) < 4:
            return
        length = chunks[3]
        expected = 4 + length + 3
        if len(chunks) >= expected and chunks[expected - 1] == 0x77:
            event.set()

    await client.start_notify(NOTIFY_UUID, on_notify)
    try:
        await client.write_gatt_char(WRITE_UUID, frame, response=True)
        await asyncio.wait_for(event.wait(), timeout=5)
    finally:
        await client.stop_notify(NOTIFY_UUID)

    length = chunks[3]
    response = bytes(chunks[: 4 + length + 3])
    if not _checksum_ok(response):
        raise FogstarBmsError(f"bad BLE response checksum: {response.hex(' ')}")
    return response[4 : 4 + length]


async def _query_with_retries(client: BleakClient, frame: bytes, retries: int = 2) -> bytes:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await _query(client, frame)
        except (TimeoutError, FogstarBmsError) as err:
            last_error = err
            await asyncio.sleep(0.2 + attempt * 0.1)
    raise FogstarBmsError(f"BLE query failed: {last_error}") from last_error


async def read_bms_ble(
    address: str | None,
    name: str | None,
    password: str,
    pair: bool,
    hass: HomeAssistant | None = None,
) -> FogstarBmsData:
    async with _BLE_CONNECTION_LOCK:
        device = await _find_device(hass, address, name)
        if device is None:
            raise FogstarBmsError("BLE device not found")

        try:
            client = await establish_connection(
                BleakClient,
                device,
                device.name or device.address,
                timeout=20,
                pair=pair,
            )
        except (BleakConnectionError, BleakError) as err:
            raise FogstarBmsError(str(err)) from err
        try:
            if password:
                authenticated = False
                for reverse_mac in (False, True):
                    try:
                        await _authenticate(client, device.address, password, reverse_mac)
                        authenticated = True
                        break
                    except Exception:
                        LOGGER.debug(
                            "BLE BMS authentication attempt failed reverse_mac=%s",
                            reverse_mac,
                            exc_info=True,
                        )
                        if not client.is_connected:
                            raise
                if not authenticated:
                    raise FogstarBmsError("BLE authentication failed")
                await asyncio.sleep(0.2)

            basic = await _query_with_retries(client, REQUESTS["basic"])
            cells = await _query_with_retries(client, REQUESTS["cells"])
            hardware = await _query_with_retries(client, REQUESTS["hardware"])
        except BleakError as err:
            raise FogstarBmsError(str(err)) from err
        finally:
            if client.is_connected:
                await client.disconnect()
        return decode_bms_payloads(basic, cells, hardware)
