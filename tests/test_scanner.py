"""Scanner + transport tests against a real (fake) Modbus/TCP server.

These run over actual localhost TCP so they exercise pymodbus framing —
the exact layer the old raw-socket scanner got wrong: fragmented
responses, stale/mismatched transaction ids, wrong unit ids, exception
PDUs, and mid-scan disconnects.
"""

from __future__ import annotations

import pytest

from custom_components.solaredge_modbus_multi.modbus_transport import ModbusTransport
from custom_components.solaredge_modbus_multi.scanner import SolarEdgeDeviceScanner
from tests.conftest import string_registers
from tests.fake_modbus_server import FakeModbusServer


@pytest.fixture(autouse=True)
def _allow_sockets(socket_enabled):
    """These tests intentionally use real localhost sockets."""
    yield


def solaredge_header_space() -> dict[int, int]:
    """SunSpec common-block header of a SolarEdge inverter at 40000."""
    registers = [0x5375, 0x6E53, 1, 65] + string_registers("SolarEdge ", 5)
    return {40000 + offset: value for offset, value in enumerate(registers)}


def other_device_space() -> dict[int, int]:
    registers = [0x5375, 0x6E53, 1, 65] + string_registers("WattNode", 5)
    return {40000 + offset: value for offset, value in enumerate(registers)}


@pytest.fixture
def make_server():
    servers: list[FakeModbusServer] = []

    async def _make(**kwargs) -> FakeModbusServer:
        server = FakeModbusServer(**kwargs)
        await server.start()
        servers.append(server)
        return server

    yield _make


def make_scanner(server: FakeModbusServer, **kwargs) -> SolarEdgeDeviceScanner:
    defaults = {
        "connect_timeout": 1.0,
        "scan_retries": 2,
        "scan_timeout": 0.3,
    }
    defaults.update(kwargs)
    return SolarEdgeDeviceScanner("127.0.0.1", server.port, **defaults)


async def test_scan_finds_inverter(make_server) -> None:
    server = await make_server(spaces={1: solaredge_header_space()})
    scanner = make_scanner(server)
    try:
        await scanner.connect()
        result = await scanner.check_list([1])
    finally:
        await scanner.disconnect()
        await server.stop()

    assert result == {"inverters": [1], "other_devices": [], "no_response": []}


async def test_scan_classifies_non_solaredge_as_other(make_server) -> None:
    server = await make_server(spaces={1: other_device_space()})
    scanner = make_scanner(server)
    try:
        await scanner.connect()
        result = await scanner.check_list([1])
    finally:
        await scanner.disconnect()
        await server.stop()

    assert result["other_devices"] == [1]
    assert result["inverters"] == []


async def test_scan_classifies_exception_pdu_as_other_device(make_server) -> None:
    """A Modbus exception response means a device exists at that ID."""
    server = await make_server(
        spaces={1: solaredge_header_space()},
        exception_units={2: 0x02},  # IllegalAddress
    )
    scanner = make_scanner(server)
    try:
        await scanner.connect()
        result = await scanner.check_list([1, 2])
    finally:
        await scanner.disconnect()
        await server.stop()

    assert result == {"inverters": [1], "other_devices": [2], "no_response": []}


async def test_scan_times_out_silent_unit(make_server) -> None:
    server = await make_server(
        spaces={1: solaredge_header_space()},
        silent_units={5},
        max_sessions=8,
    )
    scanner = make_scanner(server)
    try:
        await scanner.connect()
        result = await scanner.check_list([5, 1])
    finally:
        await scanner.disconnect()
        await server.stop()

    assert result["no_response"] == [5]
    assert result["inverters"] == [1]


async def test_scan_reassembles_fragmented_response(make_server) -> None:
    """A response split across TCP segments must still decode.

    The old raw-socket scanner did a single read(1024) and would have
    seen a truncated frame here.
    """
    server = await make_server(spaces={1: solaredge_header_space()}, fragment=True)
    scanner = make_scanner(server)
    try:
        await scanner.connect()
        result = await scanner.check_list([1])
    finally:
        await scanner.disconnect()
        await server.stop()

    assert result["inverters"] == [1]


async def test_scan_ignores_mismatched_transaction_id(make_server) -> None:
    """A stale/mismatched frame is no-response, not a phantom device.

    The old scanner classified any non-signature bytes as FOUND (an
    "other Modbus device"), turning stale frames into config errors.
    """
    server = await make_server(
        spaces={1: solaredge_header_space(), 3: solaredge_header_space()},
        wrong_txn_units={3},
        max_sessions=8,
    )
    scanner = make_scanner(server)
    try:
        await scanner.connect()
        result = await scanner.check_list([3, 1])
    finally:
        await scanner.disconnect()
        await server.stop()

    assert 3 in result["no_response"]
    assert result["other_devices"] == []
    assert 1 in result["inverters"]


async def test_scan_ignores_mismatched_unit_id(make_server) -> None:
    server = await make_server(
        spaces={1: solaredge_header_space(), 4: solaredge_header_space()},
        wrong_unit_units={4},
        max_sessions=8,
    )
    scanner = make_scanner(server)
    try:
        await scanner.connect()
        result = await scanner.check_list([4, 1])
    finally:
        await scanner.disconnect()
        await server.stop()

    assert 4 in result["no_response"]
    assert result["other_devices"] == []
    assert 1 in result["inverters"]


async def test_scan_survives_mid_scan_disconnect(make_server) -> None:
    """A unit that drops the connection must not break the rest of the scan."""
    server = await make_server(
        spaces={1: solaredge_header_space()},
        disconnect_units={2},
        max_sessions=8,
    )
    scanner = make_scanner(server)
    try:
        await scanner.connect()
        result = await scanner.check_list([2, 1])
    finally:
        await scanner.disconnect()
        await server.stop()

    assert 2 in result["no_response"]
    assert 1 in result["inverters"]


async def test_single_session_enforcement(make_server) -> None:
    """A second concurrent session is refused; the first keeps working.

    This is why the config flow aborts on already-configured hosts BEFORE
    connecting a scanner: the real inverter behaves exactly like this.
    """
    server = await make_server(spaces={1: solaredge_header_space()}, max_sessions=1)

    first = make_scanner(server)
    second = make_scanner(server, scan_retries=1, scan_timeout=0.2)
    try:
        await first.connect()
        result_second = await second.check_list([1])
        result_first = await first.check_list([1])
    finally:
        await first.disconnect()
        await second.disconnect()
        await server.stop()

    assert server.rejected_sessions > 0
    assert result_second["inverters"] == []
    assert result_first["inverters"] == [1]


async def test_transport_write_is_atomic_function_16(make_server) -> None:
    """Multi-register writes go out as one function-16 transaction."""
    server = await make_server(spaces={1: solaredge_header_space()})
    transport = ModbusTransport(
        host="127.0.0.1",
        port=server.port,
        timeout=1,
        retries=0,
        reconnect_delay=0,
        reconnect_delay_max=0,
    )
    try:
        await transport.connect()
        result = await transport.write_registers_raw(1, 57348, [1, 17274, 17530])
        assert not result.isError()
    finally:
        await transport.disconnect(clear_client=True)
        await server.stop()

    assert server.writes == [(1, 57348, [1, 17274, 17530])]
    assert transport.stats.writes == 1
    assert transport.stats.connects == 1
