from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import time

import serial


REQUESTS = {
    "basic": bytes.fromhex("dd a5 03 00 ff fd 77"),
    "cells": bytes.fromhex("dd a5 04 00 ff fc 77"),
    "hardware": bytes.fromhex("dd a5 05 00 ff fb 77"),
}


class FogstarBmsError(Exception):
    """Raised when the BMS does not return a valid frame."""


@dataclass(slots=True)
class FogstarBmsData:
    hardware: str
    voltage: float
    current: float
    state_of_charge: int
    remaining_capacity: float
    nominal_capacity: float
    cycles: int
    production_date: str
    software_version: int
    fet_state: int
    protection_flags: int
    balance_flags_low: int
    balance_flags_high: int
    cells: list[float]
    temperatures: list[float]


def _checksum_ok(frame: bytes) -> bool:
    if len(frame) < 7 or frame[0] != 0xDD or frame[-1] != 0x77:
        return False
    length = frame[3]
    if len(frame) != 4 + length + 3:
        return False
    received = (frame[4 + length] << 8) | frame[5 + length]
    total = sum(frame[2 : 4 + length])
    return ((total + received) & 0xFFFF) == 0


def _find_frame(raw: bytes, command: int) -> bytes | None:
    for start in range(len(raw)):
        if start + 6 >= len(raw) or raw[start] != 0xDD or raw[start + 1] != command:
            continue
        length = raw[start + 3]
        end = start + 4 + length + 3
        frame = raw[start:end]
        if len(frame) == end - start and _checksum_ok(frame):
            return frame
    return None


def _read_quiet(ser: serial.Serial, quiet_ms: int = 120, max_ms: int = 700) -> bytes:
    deadline = time.monotonic() + max_ms / 1000
    quiet_deadline = time.monotonic() + quiet_ms / 1000
    chunks: list[bytes] = []
    while time.monotonic() < deadline and time.monotonic() < quiet_deadline:
        waiting = ser.in_waiting
        if waiting:
            chunks.append(ser.read(waiting))
            quiet_deadline = time.monotonic() + quiet_ms / 1000
        else:
            time.sleep(0.005)
    return b"".join(chunks)


def _query_once(ser: serial.Serial, name: str) -> tuple[bytes | None, bytes]:
    request = REQUESTS[name]
    ser.reset_input_buffer()
    ser.write(request)
    ser.flush()
    raw = _read_quiet(ser)
    frame = _find_frame(raw, request[2])
    if frame is None:
        return None, raw
    return frame[4 : 4 + frame[3]], raw


def _query(ser: serial.Serial, name: str, retries: int = 3) -> bytes:
    last_raw = b""
    for attempt in range(retries + 1):
        payload, raw = _query_once(ser, name)
        if payload is not None:
            return payload
        last_raw = raw
        time.sleep(0.08 + attempt * 0.04)
    raise FogstarBmsError(f"no valid {name} frame; raw={last_raw.hex(' ')}")


def _u16(data: bytes, offset: int) -> int:
    return (data[offset] << 8) | data[offset + 1]


def _s16(data: bytes, offset: int) -> int:
    value = _u16(data, offset)
    return value - 0x10000 if value & 0x8000 else value


def _decode_date(value: int) -> str:
    year = 2000 + ((value >> 9) & 0x7F)
    month = (value >> 5) & 0x0F
    day = value & 0x1F
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return f"invalid-{value:04x}"


def _temp_c(raw: int) -> float:
    return round(raw / 10.0 - 273.15, 1)


def read_bms(port: str, baudrate: int) -> FogstarBmsData:
    with serial.Serial(port, baudrate, timeout=0.12, write_timeout=0.2) as ser:
        basic = _query(ser, "basic")
        cells = _query(ser, "cells")
        hardware = _query(ser, "hardware")

    return decode_bms_payloads(basic, cells, hardware)


def decode_bms_payloads(basic: bytes, cells: bytes, hardware: bytes) -> FogstarBmsData:
    ntc_count = basic[22]
    temperatures = [
        _temp_c(_u16(basic, 23 + i * 2))
        for i in range(ntc_count)
        if 24 + i * 2 < len(basic)
    ]

    return FogstarBmsData(
        hardware=hardware.decode("ascii", errors="replace"),
        voltage=round(_u16(basic, 0) / 100.0, 2),
        current=round(_s16(basic, 2) / 100.0, 2),
        remaining_capacity=round(_u16(basic, 4) / 100.0, 2),
        nominal_capacity=round(_u16(basic, 6) / 100.0, 2),
        cycles=_u16(basic, 8),
        production_date=_decode_date(_u16(basic, 10)),
        balance_flags_low=_u16(basic, 12),
        balance_flags_high=_u16(basic, 14),
        protection_flags=_u16(basic, 16),
        software_version=basic[18],
        state_of_charge=basic[19],
        fet_state=basic[20],
        cells=[round(_u16(cells, i) / 1000.0, 3) for i in range(0, len(cells), 2)],
        temperatures=temperatures,
    )
