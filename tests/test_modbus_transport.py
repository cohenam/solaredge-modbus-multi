"""Transport guarantees that the rest of the integration depends on.

These pin the decisions behind the modbus-connection migration rather than
incidental behaviour: how a dropped frame is classified, what a lost write
response is allowed to do, and the connection-generation bookkeeping that
replaced pymodbus's reopenable client.
"""

from __future__ import annotations

import asyncio

import pytest
from modbus_connection.exceptions import (
    ModbusConnectionError,
    ModbusError,
    ModbusExceptionError,
    ModbusProtocolError,
    ModbusTimeoutError,
)

from custom_components.solaredge_modbus_multi.const import ModbusExceptions
from custom_components.solaredge_modbus_multi.exceptions import (
    ModbusIllegalAddress,
    ModbusIllegalFunction,
    ModbusIllegalValue,
    ModbusIOError,
    ModbusReadError,
)
from custom_components.solaredge_modbus_multi.modbus_transport import ModbusTransport
from tests.conftest import connection_double


@pytest.fixture
def transport():
    """A transport whose every connection generation is a double."""
    built: list = []

    def factory(**kwargs):
        connection = connection_double()
        built.append(connection)
        return connection

    transport = ModbusTransport(
        host="192.0.2.10", port=1502, timeout=3, connection_factory=factory
    )
    transport.built = built
    return transport


async def _read(transport, *, unit=1, address=40000, count=2):
    return await transport.read_holding_registers_raw(unit, address, count)


# --- how a failed read is classified ----------------------------------------


@pytest.mark.parametrize(
    "failure",
    [ModbusTimeoutError("no answer"), ModbusConnectionError("dropped")],
    ids=["timeout", "connection"],
)
async def test_dropped_frame_is_io_error_not_timeout_error(transport, failure) -> None:
    """A dropped frame must not look like an expired whole-poll deadline.

    TimeoutError reaching the hub means the coordinator's budget ran out and
    counts against its retry limit. One unanswered request is a different
    thing and must not trip it.
    """
    await transport.connect()
    transport.built[-1].read_holding_registers.side_effect = failure

    with pytest.raises(ModbusIOError) as caught:
        await _read(transport)

    assert not isinstance(caught.value, TimeoutError)


async def test_malformed_answer_is_not_evidence_of_a_device(transport) -> None:
    """Only an exception PDU proves something is answering at an id.

    The ID scanner classifies on this: a generic library error means
    "unknown, retry", never "a device lives here".
    """
    await transport.connect()
    transport.built[-1].read_holding_registers.side_effect = ModbusError("garbage")

    with pytest.raises(ModbusIOError):
        await _read(transport)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (ModbusExceptions.IllegalAddress, ModbusIllegalAddress),
        (ModbusExceptions.IllegalFunction, ModbusIllegalFunction),
        (ModbusExceptions.IllegalValue, ModbusIllegalValue),
        (0x06, ModbusReadError),
    ],
    ids=["address", "function", "value", "other"],
)
async def test_exception_pdu_maps_to_our_hierarchy(transport, code, expected) -> None:
    """The library's exception types stop at the transport boundary."""
    await transport.connect()
    transport.built[-1].read_holding_registers.side_effect = ModbusExceptionError(code)

    with pytest.raises(expected):
        await _read(transport)


async def test_exception_pdu_keeps_the_connection(transport) -> None:
    """A device that refuses a register is healthy; do not churn the socket."""
    await transport.connect()
    first = transport.built[-1]
    first.read_holding_registers.side_effect = ModbusExceptionError(
        ModbusExceptions.IllegalAddress
    )

    with pytest.raises(ModbusIllegalAddress):
        await _read(transport)

    assert transport.stats.recycles == 0
    assert transport._connection is first


# --- connection generations --------------------------------------------------


async def test_failed_read_retires_the_generation(transport) -> None:
    """A wedged socket cannot be reopened, only replaced."""
    await transport.connect()
    first = transport.built[-1]
    first.read_holding_registers.side_effect = ModbusProtocolError("desync")

    with pytest.raises(ModbusIOError):
        await _read(transport)

    assert transport.stats.recycles == 1
    assert transport._connection is None

    await transport.connect()
    assert transport._connection is not first
    assert len(transport.built) == 2


async def test_retired_generation_cannot_touch_live_counters(transport) -> None:
    """A dying connection's callback must not report against its successor."""
    await transport.connect()
    first = transport.built[-1]
    # The transport registers exactly one lost-callback per generation.
    stale_callback = first.on_connection_lost.call_args[0][0]

    await transport.recycle()
    await transport.connect()

    stale_callback()

    assert transport.stats.connection_losses == 0

    live_callback = transport.built[-1].on_connection_lost.call_args[0][0]
    live_callback()

    assert transport.stats.connection_losses == 1


# --- writes ------------------------------------------------------------------


async def test_lost_write_response_is_never_repeated(transport) -> None:
    """A write whose answer is lost may already have been applied.

    These are power-control registers — one of them commits to flash — so
    the outcome is reported as unknown and the frame is sent exactly once.
    """
    await transport.connect()
    connection = transport.built[-1]
    connection.write_registers.side_effect = ModbusTimeoutError("no answer")

    with pytest.raises(ModbusIOError, match="may or may not have been applied"):
        await transport.write_registers_raw(1, 61696, [1])

    assert connection.write_registers.await_count == 1
    assert transport.stats.recycles == 1


async def test_write_exception_pdu_does_not_recycle(transport) -> None:
    """A refused write is an answer, so the session stays up."""
    await transport.connect()
    transport.built[-1].write_registers.side_effect = ModbusExceptionError(
        ModbusExceptions.IllegalValue
    )

    with pytest.raises(ModbusIllegalValue):
        await transport.write_registers_raw(1, 61441, [200])

    assert transport.stats.recycles == 0


# --- session hold ------------------------------------------------------------


async def test_hold_session_is_reentrant_for_its_owner(transport) -> None:
    """A device's whole read cycle runs without releasing the session."""
    await transport.connect()
    transport.built[-1].read_holding_registers.return_value = [0, 0]

    async with transport.hold_session():
        await _read(transport)
        await _read(transport)

    assert transport.stats.reads == 2


async def test_hold_session_excludes_other_tasks(transport) -> None:
    """Interleaved requests are what make SolarEdge answer for the wrong unit."""
    await transport.connect()
    transport.built[-1].read_holding_registers.return_value = [0, 0]
    started = asyncio.Event()

    async def competitor():
        started.set()
        await _read(transport, unit=2)

    async with transport.hold_session():
        task = asyncio.create_task(competitor())
        await started.wait()
        await asyncio.sleep(0)

        assert not task.done(), "another task entered the held session"

    await task
