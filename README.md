# Fogstar BMS Home Assistant Integration

Custom Home Assistant integration for a Fogstar Drift Pro Gen2 / JBD-compatible
BMS connected over Bluetooth or RS485.

## Installation

1. Add this repository to HACS as a custom repository.
2. Select category `Integration`.
3. Install `Fogstar BMS`.
4. Restart Home Assistant.
5. Add the integration from `Settings` -> `Devices & services` -> `Add integration`.

## Bluetooth

Bluetooth is recommended when monitoring multiple batteries. Add the integration
once for each battery and enter that battery's BLE address, BLE name, and BMS
password.

Typical Bluetooth settings:

```text
Connection type: Bluetooth
BLE address: A4:C1:37:...
BLE name: Battery
BMS password: your BMS password
Scan interval: 30
```

## Serial

For a USB RS485 adapter on Home Assistant OS, use the serial connection:

```text
Port: /dev/ttyUSB0
Baud rate: 9600
Scan interval: 30
```

Stable Linux serial paths such as `/dev/serial/by-id/...` are recommended over
`/dev/ttyUSB0`, because `ttyUSB` numbers can change after rebooting or moving
USB adapters.

Serial mode reads one BMS per serial port. Multi-drop RS485 with several
batteries on the same two-wire bus is not supported unless the batteries have
unique Modbus addresses.

## Entities

The integration exposes:

- Pack voltage
- Pack current
- State of charge
- Remaining capacity
- Nominal capacity
- Cycle count
- Protection flags
- FET state
- Per-cell voltage sensors
- Temperature sensors

## Safety

This integration reads BMS data over Bluetooth or RS485. It does not write
configuration values to the BMS.
