"""Write-characterization golden: entity action in, Modbus payload out.

Drives every write path the integration can take — all four write
platforms (button, number, select, switch) — through the real entity
methods and snapshots the exact (address, payload) handed to the device.
This pins current encode behavior bit-for-bit, including word order and
the int()/float() coercion each site applies, so an encoder-library swap
can prove it changes nothing.

These are live inverter power-control registers (61696 commits settings
to inverter flash), so payload drift is a hardware-safety incident, not
a test failure. The expected payloads in the fixture are plain integer
literals on purpose: they are never recomputed with
ModbusClientMixin.convert_to_registers, the helper production itself
calls, because a golden that shares an encoder with the code under test
cannot detect that encoder changing.

The fixture (tests/fixtures/write_golden.json) is committed evidence, not
a cache: a missing one fails the test rather than being regenerated,
because a gate that passes without its evidence is not a gate. To rebuild
it deliberately, run with SOLAREDGE_REGENERATE_WRITE_GOLDEN=1 — which
rewrites it and then fails, so the new payloads have to be re-verified
against the register spec before they can be committed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.solaredge_modbus_multi import button, number, select, switch

REGENERATE_ENV = "SOLAREDGE_REGENERATE_WRITE_GOLDEN"

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "write_golden.json"

# Fixed device state every write is driven from. Bits 0, 1, 2, 10 and 11
# of E_Lim_Ctl_Mode are set so the read-modify-write sites have both bits
# to preserve and bits to clear.
BASE_DECODED_MODEL = {
    "I_Power_Limit": 80,
    "I_CosPhi": 0.95,
    "PowerReduce": 100.0,
    "MaxCurrent": 32.0,
    "E_Site_Limit": 5000,
    "Ext_Prod_Max": 10000,
    "E_Lim_Ctl_Mode": 0b110000000111,
    "E_Lim_Ctl": 0,
    "ReactivePwrConfig": 0,
    "AdvPwrCtrlEn": 0x0,
}

BASE_STORAGE_CONTROL = {
    "control_mode": 4,  # Remote Control
    "ac_charge_policy": 2,  # Fixed Energy Limit -> max is 100 MWh
    "ac_charge_limit": 10.0,
    "backup_reserve": 30.0,
    "command_timeout": 600,
    "charge_limit": 5000.0,
    "discharge_limit": 4000.0,
}

# Read-modify-write sites are driven from both an all-clear register and
# a populated one, so the golden pins masking as well as encoding.
E_LIM_CTL_MODE_STATES = (0x0000, 0b110000000111)

# (entity class, native_min_value, native_max_value, mid-range inputs).
# Every entity is driven at both its own limits plus a fractional value:
# the fraction is what separates the float() sites from the int() ones.
NUMBER_CASES = (
    (number.StorageACChargeLimit, 0, 100000000, (12.5,)),
    (number.StorageBackupReserve, 0, 100, (12.5,)),
    (number.StorageCommandTimeout, 0, 86400, (12.5,)),
    (number.StorageChargeLimit, 0, 1000000, (12.5,)),
    (number.StorageDischargeLimit, 0, 1000000, (12.5,)),
    (number.SolarEdgeSiteLimit, 0, 1000000, (12.5,)),
    (number.SolarEdgeExternalProductionMax, 0, 1000000, (12.5,)),
    (number.SolarEdgeActivePowerLimitSet, 0, 100, (12.5,)),
    (number.SolarEdgeCosPhiSet, -1.0, 1.0, (0, 0.95)),
    (number.SolarEdgePowerReduce, 0, 100, (12.5,)),
    (number.SolarEdgeCurrentLimit, 0, 256, (12.5,)),
)

# (entity class, every option the entity offers). Options are spelled out
# rather than re-derived from const.py so the golden stays reproducible;
# _drive_selects asserts the lists still match what the entity exposes,
# which makes a newly added option fail loudly instead of going unpinned.
SELECT_CASES = (
    (
        select.StorageControlMode,
        (
            "Disabled",
            "Maximize Self Consumption",
            "Time of Use",
            "Backup Only",
            "Remote Control",
        ),
    ),
    (
        select.StorageACChargePolicy,
        ("Disabled", "Always Allowed", "Fixed Energy Limit", "Percent of Production"),
    ),
    (
        select.StorageDefaultMode,
        (
            "Solar Power Only (Off)",
            "Charge from Clipped Solar Power",
            "Charge from Solar Power",
            "Charge from Solar Power and Grid",
            "Discharge to Maximize Export",
            "Discharge to Minimize Import",
            "Maximize Self Consumption",
        ),
    ),
    (
        select.StorageCommandMode,
        (
            "Solar Power Only (Off)",
            "Charge from Clipped Solar Power",
            "Charge from Solar Power",
            "Charge from Solar Power and Grid",
            "Discharge to Maximize Export",
            "Discharge to Minimize Import",
            "Maximize Self Consumption",
        ),
    ),
    (select.SolaredgeLimitControl, ("Total", "Per Phase")),
    (
        select.SolarEdgeReactivePowerMode,
        ("Fixed CosPhi", "Fixed Q", "CosPhi(P)", "Q(U) + Q(P)", "RRCR"),
    ),
)

# Limit Control Mode is the one select whose payload depends on the
# current register, so it is driven separately across both states.
LIMIT_CONTROL_MODE_OPTIONS = (
    "Disabled",
    "Export Control (Export/Import Meter)",
    "Export Control (Consumption Meter)",
    "Production Control",
)


def _make_platform() -> MagicMock:
    """A device stub that satisfies every write entity's preconditions."""
    platform = MagicMock()
    platform.uid_base = "se_inv_1"
    platform.inverter_unit_id = 1
    platform.online = True
    platform.has_battery = True
    platform.global_power_control = True
    # The two APC buttons re-check this on the write path and refuse
    # unless it is exactly True.
    platform.advanced_power_control = True
    platform.decoded_model = dict(BASE_DECODED_MODEL)
    platform.decoded_storage_control = dict(BASE_STORAGE_CONTROL)
    platform.write_registers = AsyncMock()
    return platform


def _make_coordinator() -> MagicMock:
    coordinator = MagicMock()
    coordinator.async_add_listener = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator.last_update_success = True
    coordinator.data = {}
    return coordinator


def _make_config_entry() -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "write_golden"
    entry.data = {"name": "Test SolarEdge"}
    return entry


def _build(entity_cls, e_lim_ctl_mode: int | None = None):
    platform = _make_platform()
    if e_lim_ctl_mode is not None:
        platform.decoded_model["E_Lim_Ctl_Mode"] = e_lim_ctl_mode
    entity = entity_cls(platform, _make_config_entry(), _make_coordinator())
    return entity, platform


async def _capture(
    rows: list[dict],
    entity_cls,
    method: str,
    args: tuple = (),
    *,
    e_lim_ctl_mode: int | None = None,
) -> None:
    """Drive one real entity method and record what it wrote."""
    entity, platform = _build(entity_cls, e_lim_ctl_mode)
    await getattr(entity, method)(*args)

    platform.write_registers.assert_called_once()
    call = platform.write_registers.call_args.kwargs

    label = f"{method}({', '.join(repr(arg) for arg in args)})"
    if e_lim_ctl_mode is not None:
        label += f" @ E_Lim_Ctl_Mode={e_lim_ctl_mode:#06x}"

    rows.append(
        {
            "entity": entity_cls.__name__,
            "call": label,
            "address": call["address"],
            "payload": list(call["payload"]),
        }
    )


async def _drive_numbers(rows: list[dict]) -> None:
    for entity_cls, minimum, maximum, middles in NUMBER_CASES:
        # The min/max literals above are only meaningful as boundaries if
        # they are still the entity's own limits.
        probe, _ = _build(entity_cls)
        assert probe.native_min_value == minimum
        assert probe.native_max_value == maximum

        for value in (minimum, *middles, maximum):
            await _capture(rows, entity_cls, "async_set_native_value", (value,))


async def _drive_selects(rows: list[dict]) -> None:
    for entity_cls, options in SELECT_CASES:
        probe, _ = _build(entity_cls)
        assert tuple(probe.options) == options

        for option in options:
            await _capture(rows, entity_cls, "async_select_option", (option,))

    probe, _ = _build(select.SolaredgeLimitControlMode)
    assert tuple(probe.options) == LIMIT_CONTROL_MODE_OPTIONS

    for bits in E_LIM_CTL_MODE_STATES:
        for option in LIMIT_CONTROL_MODE_OPTIONS:
            await _capture(
                rows,
                select.SolaredgeLimitControlMode,
                "async_select_option",
                (option,),
                e_lim_ctl_mode=bits,
            )


async def _drive_switches(rows: list[dict]) -> None:
    for entity_cls in (
        switch.SolarEdgeExternalProduction,
        switch.SolarEdgeNegativeSiteLimit,
    ):
        for bits in E_LIM_CTL_MODE_STATES:
            for method in ("async_turn_on", "async_turn_off"):
                await _capture(rows, entity_cls, method, e_lim_ctl_mode=bits)

    # Grid Control writes a constant, so the current register is irrelevant.
    for method in ("async_turn_on", "async_turn_off"):
        await _capture(rows, switch.SolarEdgeGridControl, method)


async def _drive_buttons(rows: list[dict]) -> None:
    for entity_cls in (
        button.SolarEdgeCommitControlSettings,
        button.SolarEdgeDefaultControlSettings,
    ):
        await _capture(rows, entity_cls, "async_press")


async def test_write_payload_golden() -> None:
    """Every payload the integration can put on the wire must match."""
    rows: list[dict] = []
    await _drive_numbers(rows)
    await _drive_selects(rows)
    await _drive_switches(rows)
    await _drive_buttons(rows)

    rendered = json.dumps(rows, indent=1, sort_keys=True) + "\n"

    if os.environ.get(REGENERATE_ENV):
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(rendered)
        pytest.fail(
            f"{REGENERATE_ENV} was set, so {GOLDEN_PATH.name} was rewritten. "
            "Review the diff against the register spec, commit it, and re-run "
            "without the variable."
        )

    # Deliberately not generate-on-missing: this is the hardware gate for live
    # power-control registers, and a gate that passes when its evidence is
    # absent is not a gate.
    assert GOLDEN_PATH.exists(), (
        f"{GOLDEN_PATH} is missing. It is committed evidence, not a cache — "
        f"restore it from git. To rebuild deliberately, run with "
        f"{REGENERATE_ENV}=1."
    )

    assert json.loads(rendered) == json.loads(GOLDEN_PATH.read_text()), (
        "Modbus write payload drift detected. These are live inverter "
        "power-control registers: verify against the register spec before "
        f"accepting. If intentional, re-run with {REGENERATE_ENV}=1."
    )


async def test_refresh_button_never_writes() -> None:
    """The Refresh button is the one button that must stay read-only."""
    entity, platform = _build(button.SolarEdgeRefreshButton)
    await entity.async_press()
    platform.write_registers.assert_not_called()
