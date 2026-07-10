from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_NAME,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CONNECTION_TYPE, CONNECTION_BANK, DOMAIN
from .coordinator import FogstarBmsCoordinator
from .jbd import FogstarBmsData


@dataclass(frozen=True, kw_only=True)
class FogstarSensorDescription(SensorEntityDescription):
    value_fn: Callable[[FogstarBmsData], int | float | str | None]


PROTECTION_FLAGS: tuple[str, ...] = (
    "Cell overvoltage",
    "Cell undervoltage",
    "Pack overvoltage",
    "Pack undervoltage",
    "Charging over temperature",
    "Charging under temperature",
    "Discharging over temperature",
    "Discharging under temperature",
    "Charging overcurrent",
    "Discharging overcurrent",
    "Short circuit",
    "IC front-end error",
    "MOSFET software lock",
    "Charge timeout",
    "Unknown 0x4000",
    "Unknown 0x8000",
)


def _software_version(value: int) -> str:
    return f"{value >> 4}.{value & 0x0F}"


def _fet_state(value: int) -> str:
    charge = bool(value & 0x01)
    discharge = bool(value & 0x02)
    if charge and discharge:
        return "Charge and discharge enabled"
    if charge:
        return "Charge enabled"
    if discharge:
        return "Discharge enabled"
    return "Off"


def _protection_flags(value: int) -> str:
    if value == 0:
        return "OK"
    flags = [
        label for bit, label in enumerate(PROTECTION_FLAGS) if value & (1 << bit)
    ]
    return ", ".join(flags) if flags else f"Unknown 0x{value:04x}"


SENSORS: tuple[FogstarSensorDescription, ...] = (
    FogstarSensorDescription(
        key="voltage",
        name="Voltage",
        translation_key="voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data: data.voltage,
    ),
    FogstarSensorDescription(
        key="current",
        name="Current",
        translation_key="current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data: data.current,
    ),
    FogstarSensorDescription(
        key="state_of_charge",
        name="State of charge",
        translation_key="state_of_charge",
        native_unit_of_measurement="%",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data: data.state_of_charge,
    ),
    FogstarSensorDescription(
        key="remaining_capacity",
        name="Remaining capacity",
        translation_key="remaining_capacity",
        native_unit_of_measurement="Ah",
        icon="mdi:battery-clock",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data: data.remaining_capacity,
    ),
    FogstarSensorDescription(
        key="nominal_capacity",
        name="Nominal capacity",
        translation_key="nominal_capacity",
        native_unit_of_measurement="Ah",
        icon="mdi:battery-high",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data: data.nominal_capacity,
    ),
    FogstarSensorDescription(
        key="cycles",
        name="Cycles",
        translation_key="cycles",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: data.cycles,
    ),
    FogstarSensorDescription(
        key="software_version",
        name="Software version",
        translation_key="software_version",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _software_version(data.software_version),
    ),
    FogstarSensorDescription(
        key="protection_flags",
        name="Protection flags",
        translation_key="protection_flags",
        icon="mdi:shield-check",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _protection_flags(data.protection_flags),
    ),
    FogstarSensorDescription(
        key="fet_state",
        name="FET state",
        translation_key="fet_state",
        icon="mdi:electric-switch",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _fet_state(data.fet_state),
    ),
)

BANK_SENSORS: tuple[FogstarSensorDescription, ...] = (
    FogstarSensorDescription(
        key="min_cell_voltage",
        name="Minimum cell voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda data: min(data.cells) if data.cells else None,
    ),
    FogstarSensorDescription(
        key="max_cell_voltage",
        name="Maximum cell voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda data: max(data.cells) if data.cells else None,
    ),
    FogstarSensorDescription(
        key="cell_voltage_delta",
        name="Cell voltage delta",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda data: round(max(data.cells) - min(data.cells), 3)
        if data.cells
        else None,
    ),
    FogstarSensorDescription(
        key="highest_temperature",
        name="Highest temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: max(data.temperatures) if data.temperatures else None,
    ),
    FogstarSensorDescription(
        key="lowest_temperature",
        name="Lowest temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: min(data.temperatures) if data.temperatures else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: FogstarBmsCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        FogstarBmsSensor(coordinator, entry, description) for description in SENSORS
    ]

    if entry.data.get(CONF_CONNECTION_TYPE) == CONNECTION_BANK:
        entities.extend(
            FogstarBmsSensor(coordinator, entry, description)
            for description in BANK_SENSORS
        )
        async_add_entities(entities)
        return

    data = coordinator.data
    entities.extend(
        FogstarBmsIndexedSensor(
            coordinator,
            entry,
            f"cell_{index + 1}_voltage",
            f"Cell {index + 1} voltage",
            UnitOfElectricPotential.VOLT,
            SensorDeviceClass.VOLTAGE,
            lambda current_data, item=index: current_data.cells[item]
            if item < len(current_data.cells)
            else None,
        )
        for index in range(len(data.cells))
    )
    entities.extend(
        FogstarBmsIndexedSensor(
            coordinator,
            entry,
            f"temperature_{index + 1}",
            f"Temperature {index + 1}",
            UnitOfTemperature.CELSIUS,
            SensorDeviceClass.TEMPERATURE,
            lambda current_data, item=index: current_data.temperatures[item]
            if item < len(current_data.temperatures)
            else None,
        )
        for index in range(len(data.temperatures))
    )

    async_add_entities(entities)


class FogstarBmsEntity(CoordinatorEntity[FogstarBmsCoordinator]):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FogstarBmsCoordinator,
        entry: ConfigEntry,
        unique_id_suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{unique_id_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data[CONF_NAME],
            manufacturer="Fogstar",
            model=coordinator.data.hardware,
            sw_version=_software_version(coordinator.data.software_version),
            configuration_url="https://github.com/timmchugh11/Fogstar-BMS--Home-Assistant",
        )


class FogstarBmsSensor(FogstarBmsEntity, SensorEntity):
    entity_description: FogstarSensorDescription

    def __init__(
        self,
        coordinator: FogstarBmsCoordinator,
        entry: ConfigEntry,
        description: FogstarSensorDescription,
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | float | str | None:
        return self.entity_description.value_fn(self.coordinator.data)


class FogstarBmsIndexedSensor(FogstarBmsEntity, SensorEntity):
    def __init__(
        self,
        coordinator: FogstarBmsCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
        unit: str,
        device_class: SensorDeviceClass,
        value_fn: Callable[[FogstarBmsData], float | None],
    ) -> None:
        super().__init__(coordinator, entry, key)
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_suggested_display_precision = 3 if unit == UnitOfElectricPotential.VOLT else 1
        self._value_fn = value_fn

    @property
    def native_value(self) -> float | None:
        return self._value_fn(self.coordinator.data)
