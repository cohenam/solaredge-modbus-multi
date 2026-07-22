"""Golden snapshot of every entity the integration creates.

Guards refactors: unique_ids and names must never change, or existing
Home Assistant entity registries and long-term statistics break. The
snapshot is generated on first run (tests/fixtures/entity_golden.json)
and compared on every run after that. If a change is intentional,
delete the fixture and re-run to regenerate it.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.solaredge_modbus_multi import (
    binary_sensor,
    button,
    number,
    select,
    sensor,
    switch,
)

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "entity_golden.json"

CAPTURED_FIELDS = (
    "unique_id",
    "name",
    "device_class",
    "state_class",
    "native_unit_of_measurement",
    "entity_category",
    "icon",
)


def _make_device(uid_base: str, sunspec_did: int = 0, **attrs) -> MagicMock:
    device = MagicMock()
    device.uid_base = uid_base
    device.online = True
    # Entity constructors branch on C_SunSpec_DID; other keys are only read
    # at state-update time, so a zero default is enough for construction.
    device.decoded_model = defaultdict(int, {"C_SunSpec_DID": sunspec_did})
    device.decoded_common = defaultdict(int)
    for key, value in attrs.items():
        setattr(device, key, value)
    return device


@pytest.fixture
def full_hub() -> MagicMock:
    """A hub with one of every device type and every feature enabled."""
    hub = MagicMock()

    inverter = _make_device(
        "se_inv_1",
        sunspec_did=103,
        use_status_vendor4=True,
        global_power_control=True,
        advanced_power_control=True,
        is_mmppt=True,
        has_battery=True,
        decoded_storage_control={"control_mode": 1},
    )
    mmppt_units = []
    for unit in (0, 1):
        mmppt = MagicMock()
        mmppt.inverter = inverter
        mmppt.unit = unit
        mmppt.mmppt_key = f"mmppt_{unit}"
        mmppt_units.append(mmppt)
    inverter.mmppt_units = mmppt_units

    hub.inverters = [inverter]
    hub.meters = [_make_device("se_meter_1", sunspec_did=203)]
    hub.batteries = [_make_device("se_batt_1")]
    hub.evses = [_make_device("se_evse_1")]
    hub.option_detect_extras = True
    hub.option_storage_control = True
    hub.option_site_limit_control = True

    return hub


def _capture(entity, attr: str) -> str | None:
    try:
        value = getattr(entity, attr)
    except Exception as e:  # noqa: BLE001 - snapshot must not abort mid-run
        return f"!{type(e).__name__}"

    if value is None or isinstance(value, str):
        return value

    return f"!unexpected:{type(value).__name__}"


async def test_entity_golden_snapshot(full_hub) -> None:
    """Every created entity's identity must match the committed snapshot."""
    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "golden_entry"
    config_entry.data = {"name": "Golden"}
    config_entry.runtime_data = SimpleNamespace(hub=full_hub, coordinator=MagicMock())

    collected = []

    def add_entities(entities, update_before_add=False):
        collected.extend(entities)

    for platform in (sensor, binary_sensor, button, number, select, switch):
        await platform.async_setup_entry(hass, config_entry, add_entities)

    rows = sorted(
        (
            {
                "class": type(entity).__name__,
                **{field: _capture(entity, field) for field in CAPTURED_FIELDS},
            }
            for entity in collected
        ),
        key=lambda r: (r["class"], r["unique_id"] or ""),
    )

    if not GOLDEN_PATH.exists():
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(json.dumps(rows, indent=1) + "\n")
        return

    golden = json.loads(GOLDEN_PATH.read_text())
    assert rows == golden, (
        "Entity identity drift detected. If intentional, delete "
        f"{GOLDEN_PATH} and re-run to regenerate."
    )
