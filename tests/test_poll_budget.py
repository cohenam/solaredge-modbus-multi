"""Poll-budget characterization: the whole-poll deadline arithmetic.

coordinator_timeout derives the whole-poll deadline from the actual
device/transaction plan (hub.py). These tests pin the formula and the
timeout constants for representative fleets — 1 inverter (the deployed
reality) through 32 inverters with maximum meters and batteries — so the
Stage 2 transport rework cannot silently shrink or balloon poll budgets.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT

from custom_components.solaredge_modbus_multi.const import (
    DOMAIN,
    ConfName,
    SolarEdgeTimeouts,
)
from custom_components.solaredge_modbus_multi.hub import SolarEdgeModbusMultiHub


def test_timeout_constants() -> None:
    """The per-operation budget constants are part of the polling contract."""
    assert SolarEdgeTimeouts.Inverter == 3000
    assert SolarEdgeTimeouts.Device == 500
    assert SolarEdgeTimeouts.Init == 800
    assert SolarEdgeTimeouts.Read == 2000


@pytest.fixture
def make_fleet_hub(hass):
    def _make(
        inverters: int, meters: int, batteries: int, extras: bool
    ) -> SolarEdgeModbusMultiHub:
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN]["yaml"] = {}
        hub = SolarEdgeModbusMultiHub(
            hass,
            entry_id="budget_test",
            entry_data={
                CONF_HOST: "192.168.1.100",
                CONF_PORT: 1502,
                CONF_NAME: "Budget",
                ConfName.DEVICE_LIST: list(range(1, inverters + 1)),
            },
            entry_options={ConfName.DETECT_EXTRAS: extras},
        )
        hub.meters = [MagicMock() for _ in range(meters)]
        hub.batteries = [MagicMock() for _ in range(batteries)]
        return hub

    return _make


@pytest.mark.parametrize(
    ("inverters", "meters", "batteries", "extras", "expected_seconds"),
    [
        # One inverter + one meter: 3000 + 500.
        (1, 1, 0, False, 3.5),
        # One of everything with extras: 3000 + 500 + 500 + 3*2000.
        (1, 1, 1, True, 10.0),
        # Spec maximum: 32 inverters, 3 meters + 2 batteries each.
        # 3000 + 500*31 + 500*96 + 500*64 + 3*2000 = 104500 ms.
        (32, 96, 64, True, 104.5),
    ],
)
def test_polling_budget(
    make_fleet_hub, inverters, meters, batteries, extras, expected_seconds
) -> None:
    hub = make_fleet_hub(inverters, meters, batteries, extras)
    hub.initalized = True
    assert hub.coordinator_timeout == expected_seconds


@pytest.mark.parametrize(
    ("inverters", "extras", "expected_seconds"),
    [
        # 3000 + 800 + (500*2)*3 + (500*2)*2 + 3*2000 = 14800 ms.
        (1, True, 14.8),
        # 3000 + 800*32 + 3000 + 2000 + 3*2000*32 = 225600 ms.
        (32, True, 225.6),
        # No extras: 3000 + 800 + 3000 + 2000 = 8800 ms.
        (1, False, 8.8),
    ],
)
def test_discovery_budget(make_fleet_hub, inverters, extras, expected_seconds) -> None:
    hub = make_fleet_hub(inverters, 0, 0, extras)
    hub.initalized = False
    assert hub.coordinator_timeout == expected_seconds
