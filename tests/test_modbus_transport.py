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

from custom_components.solaredge_modbus_multi.const import (
    ModbusExceptions,
)
from custom_components.solaredge_modbus_multi.exceptions import (
    ModbusIllegalAddress,
    ModbusIllegalFunction,
    ModbusIllegalValue,
    ModbusIOError,
    ModbusReadError,
)
from custom_components.solaredge_modbus_multi.modbus_transport import ModbusTransport
from tests.conftest import connection_double
from tests.fake_modbus_server import FakeModbusServer


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


# --- cancellation, against real sockets and the real library ----------------
#
# pymodbus catches a CancelledError raised into an in-flight request and
# reports it as an I/O error, which the library reports as a timeout. These
# use a real connection because that laundering only happens in the real
# dependency — a mock would raise CancelledError straight through and prove
# nothing.


@pytest.fixture(autouse=False)
def _allow_sockets(socket_enabled):
    """These tests intentionally use real localhost sockets."""
    yield


@pytest.fixture
def make_server():
    servers: list[FakeModbusServer] = []

    async def _make(**kwargs) -> FakeModbusServer:
        server = FakeModbusServer(**kwargs)
        await server.start()
        servers.append(server)
        return server

    yield _make


def _real_transport(port: int, *, timeout: float) -> ModbusTransport:
    return ModbusTransport(host="127.0.0.1", port=port, timeout=timeout)


async def test_outer_deadline_surfaces_as_timeout_error(
    _allow_sockets, make_server
) -> None:
    """The whole-poll budget must still reach the coordinator as a timeout.

    It is what the hub's timeout counter and the retry limit key on; if a
    deadline arrived as ModbusIOError instead, a hub that never answers would
    never trip the limit.
    """
    server = await make_server(silent_units={1}, spaces={1: {}})
    transport = _real_transport(server.port, timeout=30)

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.25):
            await _read(transport)

    await transport.recycle()


async def test_explicit_cancellation_actually_cancels(
    _allow_sockets, make_server
) -> None:
    """An unload cancelling the poll must not be swallowed as an I/O error."""
    server = await make_server(silent_units={1}, spaces={1: {}})
    transport = _real_transport(server.port, timeout=30)

    task = asyncio.create_task(_read(transport))
    await asyncio.sleep(0.15)  # let the request reach the wire
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.cancelled()
    await transport.recycle()


async def test_plain_request_timeout_is_io_error(_allow_sockets, make_server) -> None:
    """Without a deadline or a cancellation it stays an ordinary I/O failure."""
    server = await make_server(silent_units={1}, spaces={1: {}})
    transport = _real_transport(server.port, timeout=0.25)

    with pytest.raises(ModbusIOError) as caught:
        await _read(transport)

    assert not isinstance(caught.value, TimeoutError)
    assert transport.stats.recycles == 1


async def test_refused_connection_is_io_error(_allow_sockets, make_server) -> None:
    """A refused connect must be normalised, not escape as a library type."""
    server = await make_server(spaces={1: {}})
    port = server.port
    await server.stop()

    transport = _real_transport(port, timeout=1)

    with pytest.raises(ModbusIOError):
        await transport.connect()


async def test_generic_write_error_is_also_an_unknown_outcome(transport) -> None:
    """A library error can be raised after function 16 already went out.

    Only an exception PDU proves the device refused and applied nothing, so
    anything else must retire the socket and be reported as unknown rather
    than as a definite failure a caller might re-send after.
    """
    await transport.connect()
    connection = transport.built[-1]
    connection.write_registers.side_effect = ModbusError("garbled")

    with pytest.raises(ModbusIOError, match="may or may not have been applied"):
        await transport.write_registers_raw(1, 61696, [1])

    assert connection.write_registers.await_count == 1
    assert transport.stats.recycles == 1


async def test_cancelled_connect_does_not_leak_a_session(transport) -> None:
    """A shielded connect can finish after its caller is cancelled.

    modbus-connection shields its internal connect task, so cancelling the
    caller does not cancel the connect — it can still succeed and leave a live
    socket. These inverters accept one session, so the transport has to retire
    the generation instead of leaving it open with nobody owning it.
    """
    connection = connection_double()

    async def shielded_connect():
        # Model the library: the cancellation does not stop the connect.
        connection.connected = True
        raise asyncio.CancelledError

    connection.connect = shielded_connect
    transport._connection_factory = lambda **kwargs: connection

    with pytest.raises(asyncio.CancelledError):
        await transport.connect()

    assert transport.stats.recycles == 1
    assert connection.close.await_count == 1, "the leaked session must be closed"
    assert transport._connection is None


@pytest.mark.parametrize(
    ("operation", "args"),
    [("read_holding_registers", (40000, 2)), ("write_registers", (61760, [0, 1]))],
    ids=["read", "write"],
)
async def test_direct_cancellation_still_retires_the_session(
    transport, operation, args
) -> None:
    """The library auto-connects inside every unit operation, and shields it.

    A cancellation landing there arrives as a bare CancelledError, which is a
    BaseException — so the ModbusError arms never see it. Without its own
    handler the socket stays open with nobody owning it, and these inverters
    accept exactly one session.
    """
    await transport.connect()
    connection = transport.built[-1]
    getattr(connection, operation).side_effect = asyncio.CancelledError

    call = (
        transport.read_holding_registers_raw(1, *args)
        if operation == "read_holding_registers"
        else transport.write_registers_raw(1, *args)
    )

    with pytest.raises(asyncio.CancelledError):
        await call

    assert transport.stats.recycles == 1
    assert connection.close.await_count == 1, "the session must be closed"
    assert transport._connection is None
