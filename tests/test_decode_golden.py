"""Decode-characterization golden: raw registers in, decoded values out.

Feeds fixed register images through the full hub decode path (inverter
common/model, MMPPT, control blocks, meter, battery) and snapshots every
decoded dict. This pins current decode behavior bit-for-bit — including
word-order choices and slice quirks — so the transport/schema refactors
(Stages 2-4) can prove they change nothing. The fixture (tests/fixtures/decode_golden.json) is committed evidence,
not a cache: a missing one fails rather than being regenerated. Rebuild
it deliberately with SOLAREDGE_REGENERATE_DECODE_GOLDEN=1, which rewrites
it and then fails so the new values get reviewed.

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
from unittest.mock import MagicMock, patch

import pytest

from custom_components.solaredge_modbus_multi.const import DOMAIN, ConfName
from custom_components.solaredge_modbus_multi.devices import decode_fields
from custom_components.solaredge_modbus_multi.hub import (
    SolarEdgeBattery,
    SolarEdgeInverter,
    SolarEdgeMeter,
    SolarEdgeModbusMultiHub,
)
from tests.conftest import (
    _inverter_common,
    _inverter_model,
    _meter_common,
    _meter_data,
    _place,
    assert_golden,
    build_synergy_full_space,
    create_exception_response,
    make_side_effect,
)

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "decode_golden.json"


@pytest.mark.parametrize(
    ("fields", "registers", "size"),
    [
        pytest.param(["value"], [1], 2, id="incomplete-chunk"),
        pytest.param(["value"], [1, 2, 3], 2, id="excess-register"),
        pytest.param(["value"], [1, 2], 0, id="zero-size"),
        pytest.param(["value"], [1, 2], -1, id="negative-size"),
    ],
)
def test_decode_fields_rejects_invalid_shape(fields, registers, size):
    decoder = MagicMock()

    with pytest.raises(ValueError):
        decode_fields(fields, registers, decoder, size)

    decoder.assert_not_called()


def build_simple_space() -> dict[int, int]:
    space: dict[int, int] = {}
    _place(space, 40000, _inverter_common("SE6000", "0003.0019.0000", "SN-B7654321"))
    _place(space, 40069, _inverter_model(101))
    # No MMPPT: the probe at 40121 sees the meter common block (DID 1),
    # which is exactly what real non-Synergy hardware returns there.
    _place(space, 40121, _meter_common("MSN-B1"))
    _place(space, 40188, _meter_data(201))
    return space


@pytest.fixture
def make_hub(hass, mock_config_entry_data):
    def _make(
        options: dict, yaml_config: dict | None = None
    ) -> SolarEdgeModbusMultiHub:
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN]["yaml"] = yaml_config or {}
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
        "custom_components.solaredge_modbus_multi.modbus_transport.ModbusConnection",
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

    assert_golden(GOLDEN_PATH, rendered, drift="Decoded-value drift detected.")


async def _init_full_devices(hub, mock_modbus_client, *, register=False):
    """Connect the hub over a full Synergy space and init all three devices.

    Returns (inverter, meter, battery, calls); `calls` records every
    (address, count) the connection served. With `register=True` the
    devices are attached to the hub and it is marked initialized, so
    `async_refresh_modbus_data` drives them.
    """
    calls: list[tuple[int, int]] = []
    client = mock_modbus_client.return_value
    client.read_holding_registers.side_effect = make_side_effect(
        build_synergy_full_space(), {}, calls
    )

    await hub.connect()
    inverter = SolarEdgeInverter(device_id=1, hub=hub)
    await inverter.init_device()
    meter = SolarEdgeMeter(device_id=1, meter_id=1, hub=hub)
    await meter.init_device()
    battery = SolarEdgeBattery(device_id=1, battery_id=1, hub=hub)
    await battery.init_device()
    if register:
        hub.inverters.append(inverter)
        hub.meters.append(meter)
        hub.batteries.append(battery)
        hub.initalized = True
    return inverter, meter, battery, calls


async def _collect_cycles(hub, calls, count) -> list[list[tuple[int, int]]]:
    """Run `count` refresh cycles, returning the physical reads each made."""
    cycles: list[list[tuple[int, int]]] = []
    for _ in range(count):
        calls.clear()
        await hub.async_refresh_modbus_data()
        cycles.append(list(calls))
    return cycles


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
    inverter, meter, battery, calls = await _init_full_devices(hub, mock_modbus_client)
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


async def test_transaction_counts_with_poll_groups(
    make_hub, mock_modbus_client, mock_config_entry_options
) -> None:
    """Per-group cadences thin the read sequence by whole blocks.

    Drives the real refresh loop (not the device reads directly) so the
    hub-level meter/battery/EVSE gating is exercised alongside the
    in-device status and MMPPT guards. The literals below are derived from
    the multipliers, not hand-copied: change a multiplier and the expected
    sequence changes with it.
    """
    options = {
        **mock_config_entry_options,
        ConfName.DETECT_EXTRAS: False,
        ConfName.SLOW_POLL_MULTIPLIER: 6,
    }
    hub = make_hub(options, {"poll": {"status": 6, "battery": 3, "mmppt": 6}})
    inverter, _, _, calls = await _init_full_devices(
        hub, mock_modbus_client, register=True
    )

    cycles = await _collect_cycles(hub, calls, 6)

    core = [(40044, 65), (40238, 107)]  # inverter model + meter
    battery_block = [(57668, 86)]

    # Cycle 0 is aligned for every group; then status/mmppt (6) and
    # battery (3) drop out until their multiple comes round again.
    assert cycles[0] == [
        (40044, 65),  # inverter model            core
        (40119, 2),  # I_Status_Vendor4          status
        (40123, 48),  # MMPPT data                mmppt
        (40113, 2),  # Grid status               status
        (40238, 107),  # Meter data (Synergy +50)  core
        (57668, 86),  # Battery data              battery
    ]
    assert cycles[1] == core
    assert cycles[2] == core
    assert cycles[3] == [*core, *battery_block]
    assert cycles[4] == core
    assert cycles[5] == core

    # 6 aligned cycles: 12 core + 2 status + 1 mmppt + 2 battery = 17.
    assert sum(len(cycle) for cycle in cycles) == 17

    # A skipped group must keep its decoded values, not drop them.
    assert "I_Grid_Status" in inverter.decoded_model
    assert "I_Status_Vendor4" in inverter.decoded_model


async def test_poll_group_defaults_reproduce_untiered_reads(
    make_hub, mock_modbus_client, mock_config_entry_options
) -> None:
    """With no YAML poll config nothing is thinned: same sequence every cycle."""
    options = {**mock_config_entry_options, ConfName.DETECT_EXTRAS: False}
    hub = make_hub(options)
    _, _, _, calls = await _init_full_devices(hub, mock_modbus_client, register=True)

    cycles = await _collect_cycles(hub, calls, 4)

    assert cycles[0] == cycles[1] == cycles[2] == cycles[3]
