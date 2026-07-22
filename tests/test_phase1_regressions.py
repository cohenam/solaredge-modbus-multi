"""Regression tests for the Phase 1 bug fixes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from pymodbus.client.mixin import ModbusClientMixin

from custom_components.solaredge_modbus_multi.const import (
    SUNSPEC_SF_RANGE,
    ModbusExceptions,
    SunSpecNotImpl,
)
from custom_components.solaredge_modbus_multi.hub import (
    DeviceIsEVSE,
    HubInitFailed,
    ModbusReadError,
    SolarEdgeInverter,
)
from tests.conftest import (
    create_exception_response,
    create_modbus_response,
    registers_from_values,
)

FLOAT32 = ModbusClientMixin.DATATYPE.FLOAT32
UINT32 = ModbusClientMixin.DATATYPE.UINT32


@pytest.fixture
def mock_hub(hass, mock_config_entry_data, mock_config_entry_options):
    """Create a hub instance backed by the standard mock entry."""
    from custom_components.solaredge_modbus_multi.const import DOMAIN
    from custom_components.solaredge_modbus_multi.hub import SolarEdgeModbusMultiHub

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["yaml"] = {}

    return SolarEdgeModbusMultiHub(
        hass,
        entry_id="test_entry",
        entry_data=mock_config_entry_data,
        entry_options=mock_config_entry_options,
    )


def _apc_block2_registers() -> list[int]:
    """Second Advanced Power Control block (61782, 84 registers).

    Layout: [0:32] float32 x16, [32:36] uint32 x2, [36:52] float32 x8,
    [52:56] uint32 x2, [56:84] float32 x14.
    """
    return registers_from_values(
        *[(1.5, FLOAT32)] * 16,
        (300, UINT32),  # PwrFrqDeratingResetTime
        (600, UINT32),  # PwrFrqDeratingGradTime
        *[(2.5, FLOAT32)] * 8,
        (2, UINT32),  # ReactQVsVgType
        (120, UINT32),  # PwrSoftStartTime
        *[(3.5, FLOAT32)] * 14,
    )


def _inverter_read_side_effect(
    mock_inverter_registers,
    mock_inverter_model_registers,
    overrides=None,
):
    """Route mocked reads by address, defaulting unknown blocks to zeros."""
    overrides = overrides or {}

    def side_effect(*args, **kwargs):
        address = kwargs.get("address", args[0] if args else 0)
        count = kwargs.get("count", args[1] if len(args) > 1 else 10)
        if address in overrides:
            return overrides[address]
        if address == 40000:
            return create_modbus_response(mock_inverter_registers)
        if address == 40044:
            return create_modbus_response([0] * 25 + mock_inverter_model_registers)
        if address == 40121:
            return create_exception_response(ModbusExceptions.IllegalAddress)
        return create_modbus_response([0] * count)

    return side_effect


async def test_apc_uint32_fields_decode_as_integers(
    mock_hub, mock_modbus_client, mock_inverter_registers, mock_inverter_model_registers
) -> None:
    """The four uint32 APC settings must decode as ints, not floats."""
    mock_hub._detect_extras = True

    mock_client = mock_modbus_client.return_value
    mock_client.read_holding_registers.side_effect = _inverter_read_side_effect(
        mock_inverter_registers,
        mock_inverter_model_registers,
        overrides={
            61696: create_modbus_response([0] * 86),
            61782: create_modbus_response(_apc_block2_registers()),
        },
    )

    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient",
        mock_modbus_client,
    ):
        await mock_hub.connect()
        inverter = SolarEdgeInverter(device_id=1, hub=mock_hub)
        await inverter.init_device()
        await inverter.read_modbus_data()

    assert inverter.advanced_power_control is True
    assert inverter.decoded_model["PwrFrqDeratingResetTime"] == 300
    assert inverter.decoded_model["PwrFrqDeratingGradTime"] == 600
    assert inverter.decoded_model["ReactQVsVgType"] == 2
    assert inverter.decoded_model["PwrSoftStartTime"] == 120
    for key in (
        "PwrFrqDeratingResetTime",
        "PwrFrqDeratingGradTime",
        "ReactQVsVgType",
        "PwrSoftStartTime",
    ):
        assert isinstance(inverter.decoded_model[key], int)
    # Neighboring float32 fields still decode as floats
    assert inverter.decoded_model["PwrVsFreqY_0"] == pytest.approx(1.5)
    assert inverter.decoded_model["DisconnectAtZeroPwrLim"] == pytest.approx(3.5)


async def test_apc_illegal_function_disables_feature(
    mock_hub, mock_modbus_client, mock_inverter_registers, mock_inverter_model_registers
) -> None:
    """IllegalFunction on an optional feature block must not fail the poll."""
    mock_hub._detect_extras = True

    mock_client = mock_modbus_client.return_value
    mock_client.read_holding_registers.side_effect = _inverter_read_side_effect(
        mock_inverter_registers,
        mock_inverter_model_registers,
        overrides={
            61440: create_exception_response(ModbusExceptions.IllegalFunction),
            61696: create_exception_response(ModbusExceptions.IllegalFunction),
        },
    )

    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient",
        mock_modbus_client,
    ):
        await mock_hub.connect()
        inverter = SolarEdgeInverter(device_id=1, hub=mock_hub)
        await inverter.init_device()
        await inverter.read_modbus_data()

    assert inverter.global_power_control is False
    assert inverter.advanced_power_control is False


async def test_evse_init_failure_raises_hub_init_failed(mock_hub) -> None:
    """A failing EVSE init must surface as HubInitFailed, not a raw error."""
    with (
        patch.object(
            SolarEdgeInverter, "init_device", side_effect=DeviceIsEVSE("EVSE")
        ),
        patch(
            "custom_components.solaredge_modbus_multi.hub.SolarEdgeEVSE"
        ) as mock_evse_cls,
        patch.object(mock_hub, "connect", new=AsyncMock()),
        patch.object(mock_hub, "disconnect", new=AsyncMock()) as mock_disconnect,
    ):
        mock_evse_cls.return_value.init_device = AsyncMock(
            side_effect=ModbusReadError("no response")
        )
        mock_hub._client = MagicMock()

        with pytest.raises(HubInitFailed):
            await mock_hub._async_init_solaredge()

        mock_disconnect.assert_awaited()


async def test_write_error_raises_before_sleeping(mock_hub, mock_modbus_client) -> None:
    """A failed write must raise immediately instead of sleeping first."""
    mock_hub._sleep_after_write = 3

    mock_client = mock_modbus_client.return_value
    mock_client.write_registers.return_value = create_exception_response(
        ModbusExceptions.IllegalAddress
    )

    with (
        patch(
            "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient",
            mock_modbus_client,
        ),
        patch(
            "custom_components.solaredge_modbus_multi.hub.asyncio.sleep",
            new=AsyncMock(),
        ) as mock_sleep,
    ):
        await mock_hub.connect()

        with pytest.raises(HomeAssistantError):
            await mock_hub.write_registers(unit=1, address=57348, payload=[1])

        mock_sleep.assert_not_awaited()
        assert mock_hub.has_write is None


class TestSfPrecision:
    """suggested_display_precision guard for scale factor edge cases."""

    @pytest.fixture
    def sensor(self):
        from custom_components.solaredge_modbus_multi.sensor import ACCurrentSensor

        platform = MagicMock()
        platform.uid_base = "se_inv_1"
        platform.decoded_model = {"C_SunSpec_DID": 101, "AC_Current_SF": -2}
        return ACCurrentSensor(platform, MagicMock(), MagicMock())

    @pytest.mark.parametrize(
        ("sf", "expected"),
        [
            (-3, 3),
            (-1, 1),
            (0, 0),
            (2, 0),  # positive SF scales up: whole numbers
            (SunSpecNotImpl.INT16, None),
            (-32768, None),
            (11, None),  # outside SUNSPEC_SF_RANGE
        ],
    )
    def test_precision(self, sensor, sf, expected):
        sensor._platform.decoded_model["AC_Current_SF"] = sf
        assert sensor.suggested_display_precision == expected

    def test_precision_missing_key(self, sensor):
        del sensor._platform.decoded_model["AC_Current_SF"]
        assert sensor.suggested_display_precision is None

    def test_range_sanity(self):
        assert -32768 not in SUNSPEC_SF_RANGE
