# Fogstar BMS Home Assistant Integration

Custom Home Assistant integration for a Fogstar Drift Pro Gen2 / JBD-compatible
BMS connected over Bluetooth or RS485.

This project is an independent community integration and is not affiliated with,
endorsed by, or sponsored by Fogstar. Product names and trademarks belong to
their respective owners.

## Installation

1. Add this repository to HACS as a custom repository.
2. Select category `Integration`.
3. Install `Fogstar BMS`.
4. Restart Home Assistant.
5. Add the integration from `Settings` -> `Devices & services` -> `Add integration`.

## Bluetooth

Bluetooth is recommended when monitoring multiple batteries. Add the integration
once for each battery, choose the battery from the discovered Bluetooth device
list, and enter the BMS password.

Typical Bluetooth settings:

```text
Connection type: Bluetooth
Bluetooth device: Battery (...)
BMS password: your BMS password
Scan interval: 30
```

If discovery does not show the battery, choose the manual option and enter the
BLE address and BLE name yourself.

## Battery bank

After adding two or more batteries, add another integration entry and choose
`Battery bank`. Select the batteries that belong to the same parallel bank.

The bank entry reuses data from the selected battery entries and does not open
additional Bluetooth or serial connections.

For a parallel bank, the integration reports:

- Average pack voltage
- Sum of pack current
- Sum of remaining capacity
- Sum of nominal capacity
- Lowest per-pack capacity-based state of charge
- Highest and lowest temperature
- Minimum, maximum, and delta cell voltage
- Combined protection status

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
