"""Decode-characterization golden: raw registers in, decoded values out.

Feeds fixed register images through the full hub decode path (inverter
common/model, MMPPT, control blocks, meter, battery) and snapshots every
decoded dict. This pins current decode behavior bit-for-bit — including
word-order choices and slice quirks — so the transport/schema refactors
(Stages 2-4) can prove they change nothing. Generated on first run
(tests/fixtures/decode_golden.json); if a change is intentional, delete
the fixture and re-run to regenerate.

Two scenarios cover both register-layout families:
- "synergy_full": three-phase 103 + MMPPT(2 units) + Synergy meter offset
  (+50) + battery + every control block + vendor status 40119.
- "simple": single-phase 101, no MMPPT (meter common at the probe
  address, as on real non-Synergy hardware), grid-status probe rejected,
  all extras options off.

The address space is one flat {address: value} dict per scenario, so
overlapping reads (40000/69 vs 40044/65; MMPPT probe vs meter common)
are served consistently, exactly like a real device.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pymodbus.client.mixin import ModbusClientMixin

from custom_components.solaredge_modbus_multi.const import DOMAIN, ConfName
from custom_components.solaredge_modbus_multi.hub import (
    SolarEdgeBattery,
    SolarEdgeInverter,
    SolarEdgeMeter,
    SolarEdgeModbusMultiHub,
)
from tests.conftest import (
    create_exception_response,
    create_modbus_response,
    registers_from_values,
    string_registers,
)

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "decode_golden.json"

DT = ModbusClientMixin.DATATYPE


def s16(val: int) -> int:
    return val & 0xFFFF


def _place(space: dict[int, int], address: int, registers: list[int]) -> None:
    for offset, value in enumerate(registers):
        space[address + offset] = value


def _inverter_common(model: str, version: str, serial: str) -> list[int]:
    regs = registers_from_values((0x53756E53, DT.UINT32), word_order="big")
    regs += [1, 65]  # C_SunSpec_DID, C_SunSpec_Length
    regs += string_registers("SolarEdge", 16)
    regs += string_registers(model, 16)
    regs += string_registers("E0", 8)
    regs += string_registers(version, 8)
    regs += string_registers(serial, 16)
    regs += [1]  # C_Device_address
    return regs


def _inverter_model(did: int) -> list[int]:
    """Model 101/103 block (40 registers at 40069), healthy values."""
    return [
        did,  # C_SunSpec_DID
        50,  # C_SunSpec_Length
        120,  # AC_Current
        40,  # AC_Current_A
        40,  # AC_Current_B
        40,  # AC_Current_C
        s16(-1),  # AC_Current_SF
        4000,  # AC_Voltage_AB
        4001,  # AC_Voltage_BC
        4002,  # AC_Voltage_CA
        2310,  # AC_Voltage_AN
        2311,  # AC_Voltage_BN
        2312,  # AC_Voltage_CN
        s16(-1),  # AC_Voltage_SF
        25000,  # AC_Power
        0,  # AC_Power_SF
        5001,  # AC_Frequency
        s16(-2),  # AC_Frequency_SF
        25500,  # AC_VA
        0,  # AC_VA_SF
        s16(-1200),  # AC_var
        0,  # AC_var_SF
        s16(-9800),  # AC_PF
        s16(-2),  # AC_PF_SF
        *registers_from_values((123_456_789, DT.UINT32), word_order="big"),
        0,  # AC_Energy_WH_SF
        330,  # I_DC_Current
        s16(-1),  # I_DC_Current_SF
        7500,  # I_DC_Voltage
        s16(-1),  # I_DC_Voltage_SF
        25400,  # I_DC_Power
        0,  # I_DC_Power_SF
        451,  # I_Temp_Cab
        452,  # I_Temp_Sink
        0,  # I_Temp_Trns
        0,  # I_Temp_Other
        s16(-1),  # I_Temp_SF
        4,  # I_Status (MPPT)
        0,  # I_Status_Vendor
    ]


def _mmppt_block() -> list[int]:
    """Model 160 data (48 registers at 40123, 2 Synergy units)."""
    regs = [s16(-2), s16(-1), 0, 0]  # DCA/DCV/DCW/DCWH SFs
    regs += registers_from_values((0, DT.UINT32), word_order="big")  # Events
    regs += [2, 0]  # N units, TmsPer
    for unit in range(2):
        regs += [unit + 1]  # ID
        regs += string_registers(f"MPPT-{unit}", 8)  # IDStr
        regs += [55 + unit, 7500 + unit, 12500 + unit]  # DCA, DCV, DCW
        regs += registers_from_values((6_100_000 + unit, DT.UINT32), word_order="big")
        regs += registers_from_values((0, DT.UINT32), word_order="big")  # Tms
        regs += [380 + unit, 4]  # Tmp, DCSt
        regs += registers_from_values((0, DT.UINT32), word_order="big")  # DCEvt
    assert len(regs) == 48
    return regs


def _meter_common(serial: str) -> list[int]:
    regs = [1, 65]  # C_SunSpec_DID, C_SunSpec_Length
    regs += string_registers("WattNode", 16)
    regs += string_registers("WNC-3Y-400-MB", 16)
    regs += string_registers("Export+Import", 8)
    regs += string_registers("25", 8)
    regs += string_registers(serial, 16)
    regs += [2]  # C_Device_address
    return regs


def _meter_data(did: int) -> list[int]:
    """Meter model block (107 registers), mirroring the hub's exact slices.

    The uint32 stream is scattered to positions [38:54]+[55:70]+[71:104]+
    [105:107] — the same index expressions the decode concatenates. Note
    register 70 is never read and register 71 feeds BOTH the M_VAh_SF
    int16 and the uint32 stream; the golden pins that behavior as-is.
    """
    regs = [0] * 107
    regs[0], regs[1] = did, 105
    for position, value in zip(range(2, 38), range(-18, 18), strict=True):
        regs[position] = s16(value)
    regs[54] = 0  # AC_Energy_WH_SF
    regs[104] = s16(-2)  # M_varh_SF

    stream: list[int] = []
    for index in range(33):
        stream += registers_from_values(
            (1_000_000 + 1_000 * index, DT.UINT32), word_order="big"
        )
    positions = (
        list(range(38, 54))
        + list(range(55, 70))
        + list(range(71, 104))
        + list(range(105, 107))
    )
    for position, value in zip(positions, stream, strict=True):
        regs[position] = value
    return regs


def _battery_common() -> list[int]:
    regs = string_registers("SolarEdge", 16)
    regs += string_registers("Home Battery 48V", 16)
    regs += string_registers("DCDC 2.0.15", 16)
    regs += string_registers("BSN-4242", 16)
    regs += [3, 0]  # B_Device_Address, pad
    regs += registers_from_values((9700.0, DT.FLOAT32))
    assert len(regs) == 68
    return regs


def _battery_data() -> list[int]:
    regs = [0] * 86
    _scatter = registers_from_values(
        (5000.0, DT.FLOAT32),  # B_MaxChargePower
        (5000.0, DT.FLOAT32),  # B_MaxDischargePower
        (7500.0, DT.FLOAT32),  # B_MaxChargePeakPower
        (7500.0, DT.FLOAT32),  # B_MaxDischargePeakPower
    )
    regs[0:8] = _scatter
    regs[40:50] = registers_from_values(
        (28.5, DT.FLOAT32),  # B_Temp_Average
        (30.0, DT.FLOAT32),  # B_Temp_Max
        (400.0, DT.FLOAT32),  # B_DC_Voltage
        (12.5, DT.FLOAT32),  # B_DC_Current
        (5000.0, DT.FLOAT32),  # B_DC_Power
    )
    regs[50:58] = registers_from_values(
        (3_500_000, DT.UINT64),  # B_Export_Energy_WH
        (4_200_000, DT.UINT64),  # B_Import_Energy_WH
    )
    regs[58:66] = registers_from_values(
        (9700.0, DT.FLOAT32),  # B_Energy_Max
        (8000.0, DT.FLOAT32),  # B_Energy_Available
        (99.0, DT.FLOAT32),  # B_SOH
        (82.5, DT.FLOAT32),  # B_SOE
    )
    regs[66:70] = registers_from_values(
        (3, DT.UINT32),  # B_Status
        (0, DT.UINT32),  # B_Status_Vendor
    )
    regs[70:86] = [0] * 16  # event logs
    return regs


def _control_blocks(space: dict[int, int]) -> None:
    """GPC, APC, site limit and storage blocks (all little word order)."""
    _place(
        space,
        61440,
        registers_from_values((0, DT.UINT16), (100, DT.UINT16), (1.0, DT.FLOAT32)),
    )

    apc1 = [0] * 86
    apc1[0:2] = registers_from_values((0, DT.INT16), (0, DT.INT16))
    apc1[2:6] = registers_from_values((1, DT.INT32), (4, DT.INT32))
    apc1[6:8] = registers_from_values((60, DT.UINT32))
    apc1[8:10] = registers_from_values((100, DT.INT32))
    block1_floats = [(float(n), DT.FLOAT32) for n in range(1, 29)]
    apc1[10:66] = registers_from_values(*block1_floats)
    apc1[66:70] = registers_from_values((1, DT.INT32), (0, DT.INT32))
    apc1[70:86] = registers_from_values(
        *[(float(n), DT.FLOAT32) for n in range(29, 37)]
    )
    _place(space, 61696, apc1)

    apc2 = [0] * 84
    apc2[0:32] = registers_from_values(
        *[(float(n), DT.FLOAT32) for n in range(101, 117)]
    )
    apc2[32:36] = registers_from_values((300, DT.UINT32), (600, DT.UINT32))
    apc2[36:52] = registers_from_values(
        *[(float(n), DT.FLOAT32) for n in range(117, 125)]
    )
    apc2[52:56] = registers_from_values((900, DT.UINT32), (0, DT.UINT32))
    apc2[56:84] = registers_from_values(
        *[(float(n), DT.FLOAT32) for n in range(125, 139)]
    )
    _place(space, 61782, apc2)

    _place(
        space,
        57344,
        registers_from_values((1, DT.UINT16), (0, DT.UINT16), (15000.0, DT.FLOAT32)),
    )
    _place(space, 57362, registers_from_values((18000.0, DT.FLOAT32)))

    storage = registers_from_values((1, DT.UINT16), (1, DT.UINT16))
    storage += registers_from_values((6600.0, DT.FLOAT32), (25.0, DT.FLOAT32))
    storage += registers_from_values((7, DT.UINT16))
    storage += registers_from_values((3600, DT.UINT32))
    storage += registers_from_values((3, DT.UINT16))
    storage += registers_from_values((5000.0, DT.FLOAT32), (5000.0, DT.FLOAT32))
    assert len(storage) == 14
    _place(space, 57348, storage)


def build_synergy_full_space() -> dict[int, int]:
    space: dict[int, int] = {}
    _place(space, 40000, _inverter_common("SE100K", "0004.0021.0000", "SN-A1234567"))
    _place(space, 40069, _inverter_model(103))
    _place(space, 40113, registers_from_values((1, DT.UINT32)))  # I_Grid_Status
    _place(
        space, 40119, registers_from_values((0x00030000, DT.UINT32), word_order="big")
    )
    _place(space, 40121, [160, 48])  # MMPPT header
    _place(space, 40123, _mmppt_block())
    _place(space, 40171, _meter_common("MSN-A777"))  # Synergy: 40121 + 50
    _place(space, 40238, _meter_data(203))
    _place(space, 57600, _battery_common())
    _place(space, 57668, _battery_data())
    _control_blocks(space)
    return space


def build_simple_space() -> dict[int, int]:
    space: dict[int, int] = {}
    _place(space, 40000, _inverter_common("SE6000", "0003.0019.0000", "SN-B7654321"))
    _place(space, 40069, _inverter_model(101))
    # No MMPPT: the probe at 40121 sees the meter common block (DID 1),
    # which is exactly what real non-Synergy hardware returns there.
    _place(space, 40121, _meter_common("MSN-B1"))
    _place(space, 40188, _meter_data(201))
    return space


def make_side_effect(space, overrides, calls):
    def side_effect(*args, **kwargs):
        address = kwargs.get("address", args[0] if args else 0)
        count = kwargs.get("count", args[1] if len(args) > 1 else 1)
        calls.append((address, count))
        if address in overrides:
            return overrides[address]
        return create_modbus_response(
            [space.get(address + offset, 0) for offset in range(count)]
        )

    return side_effect


@pytest.fixture
def make_hub(hass, mock_config_entry_data):
    def _make(options: dict) -> SolarEdgeModbusMultiHub:
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN]["yaml"] = {}
        return SolarEdgeModbusMultiHub(
            hass,
            entry_id="decode_golden",
            entry_data=mock_config_entry_data,
            entry_options=options,
        )

    return _make


async def _run_scenario(
    hub, mock_modbus_client, space, overrides, with_battery: bool
) -> tuple[dict, list]:
    calls: list[tuple[int, int]] = []
    client = mock_modbus_client.return_value
    client.read_holding_registers.side_effect = make_side_effect(
        space, overrides, calls
    )

    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient",
        mock_modbus_client,
    ):
        await hub.connect()

        inverter = SolarEdgeInverter(device_id=1, hub=hub)
        await inverter.init_device()
        meter = SolarEdgeMeter(device_id=1, meter_id=1, hub=hub)
        await meter.init_device()
        battery = None
        if with_battery:
            battery = SolarEdgeBattery(device_id=1, battery_id=1, hub=hub)
            await battery.init_device()
            hub.batteries.append(battery)

        calls.clear()
        hub.slow_poll_due = True
        await inverter.read_modbus_data()
        await meter.read_modbus_data()
        if battery is not None:
            await battery.read_modbus_data()

    snapshot = {
        "inverter": {
            "uid_base": inverter.uid_base,
            "use_status_vendor4": inverter.use_status_vendor4,
            "decoded_common": inverter.decoded_common,
            "decoded_model": inverter.decoded_model,
            "decoded_mmppt": inverter.decoded_mmppt,
            "decoded_storage_control": inverter.decoded_storage_control,
        },
        "meter_1": {
            "uid_base": meter.uid_base,
            "start_address": meter.start_address,
            "decoded_common": meter.decoded_common,
            "decoded_model": meter.decoded_model,
        },
    }
    if battery is not None:
        snapshot["battery_1"] = {
            "uid_base": battery.uid_base,
            "decoded_common": battery.decoded_common,
            "decoded_model": battery.decoded_model,
        }
    return snapshot, calls


async def test_decode_golden_snapshot(
    make_hub, mock_modbus_client, mock_config_entry_options
) -> None:
    """Every decoded value must match the committed snapshot."""
    full_options = {
        **mock_config_entry_options,
        ConfName.DETECT_EXTRAS: True,
        ConfName.ADV_STORAGE_CONTROL: True,
        ConfName.ADV_SITE_LIMIT_CONTROL: True,
    }
    synergy_snapshot, _ = await _run_scenario(
        make_hub(full_options),
        mock_modbus_client,
        build_synergy_full_space(),
        overrides={},
        with_battery=True,
    )

    simple_snapshot, _ = await _run_scenario(
        make_hub(dict(mock_config_entry_options)),
        mock_modbus_client,
        build_simple_space(),
        overrides={40113: create_exception_response(2)},  # IllegalAddress
        with_battery=False,
    )

    rows = {"synergy_full": synergy_snapshot, "simple": simple_snapshot}
    rendered = json.dumps(rows, indent=1, sort_keys=True) + "\n"

    if not GOLDEN_PATH.exists():
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(rendered)
        return

    assert json.loads(rendered) == json.loads(GOLDEN_PATH.read_text()), (
        "Decoded-value drift detected. If intentional, delete "
        f"{GOLDEN_PATH} and re-run to regenerate."
    )


async def test_transaction_counts_per_cycle(
    make_hub, mock_modbus_client, mock_config_entry_options
) -> None:
    """The exact read sequence per cycle tier is part of the contract.

    Slow cycles read the control blocks; fast cycles must not. A change
    here means the transaction plan changed — intentional changes update
    these literals (and re-baseline in Stage 5).
    """
    full_options = {
        **mock_config_entry_options,
        ConfName.DETECT_EXTRAS: True,
        ConfName.ADV_STORAGE_CONTROL: True,
        ConfName.ADV_SITE_LIMIT_CONTROL: True,
    }
    hub = make_hub(full_options)
    space = build_synergy_full_space()
    calls: list[tuple[int, int]] = []
    client = mock_modbus_client.return_value
    client.read_holding_registers.side_effect = make_side_effect(space, {}, calls)

    with patch(
        "custom_components.solaredge_modbus_multi.hub.AsyncModbusTcpClient",
        mock_modbus_client,
    ):
        await hub.connect()
        inverter = SolarEdgeInverter(device_id=1, hub=hub)
        await inverter.init_device()
        meter = SolarEdgeMeter(device_id=1, meter_id=1, hub=hub)
        await meter.init_device()
        battery = SolarEdgeBattery(device_id=1, battery_id=1, hub=hub)
        await battery.init_device()
        hub.batteries.append(battery)

        async def one_cycle() -> list[tuple[int, int]]:
            calls.clear()
            await inverter.read_modbus_data()
            await meter.read_modbus_data()
            await battery.read_modbus_data()
            return list(calls)

        hub.slow_poll_due = True
        slow_cycle = await one_cycle()
        hub.slow_poll_due = False
        fast_cycle = await one_cycle()

    assert slow_cycle == [
        (40044, 65),  # C_Version + inverter model (merged read)
        (40119, 2),  # I_Status_Vendor4
        (40123, 48),  # MMPPT data
        (61440, 4),  # Global Dynamic Power Control
        (61696, 86),  # Advanced Power Control block 1
        (61782, 84),  # Advanced Power Control block 2
        (57344, 4),  # Site limit
        (57362, 2),  # External production max
        (40113, 2),  # Grid status
        (57348, 14),  # Storage control
        (40238, 107),  # Meter data (Synergy +50)
        (57668, 86),  # Battery data
    ]
    assert fast_cycle == [
        (40044, 65),
        (40119, 2),
        (40123, 48),
        (40113, 2),
        (40238, 107),
        (57668, 86),
    ]
