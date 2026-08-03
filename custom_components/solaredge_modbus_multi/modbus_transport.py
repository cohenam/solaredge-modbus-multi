"""Home Assistant-independent Modbus/TCP transport.

One ModbusTransport = one serialized Modbus/TCP session (SolarEdge
inverters accept a single session; the server closes it after ~2 minutes
idle and the next call reconnects reactively — no keepalive on purpose).

The link itself is `modbus-connection`, which connects on demand and
drops its client when the socket goes away. What stays here is
everything that library has no equivalent for:

* the session lock with task reentrancy, so one device's whole read cycle
  is uninterrupted — SolarEdge firmware answers with the wrong unit id
  when requests interleave;
* connection *generations*: the library's close() is permanent, so
  recovering from a wedged socket means building a new connection rather
  than reopening one, and a dead generation's callbacks must not touch
  live state;
* PollStats counters for diagnostics.

Error policy: a per-request timeout is mapped to ModbusIOError, NOT left
as a TimeoutError. TimeoutError reaching the hub means the whole-poll
deadline expired, which trips the coordinator's retry limit; a single
dropped frame must not be mistaken for that. Retry policy stays with the
callers — the library issues each request once, and the enclosing
deadlines (2 s detection probes, the whole-poll budget) are what bound
it. Do not enlarge deadlines here: the fast-poll envelope depends on
fast failure.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from modbus_connection import ModbusTcpParams
from modbus_connection.exceptions import (
    ModbusConnectionError,
    ModbusError,
    ModbusExceptionError,
    ModbusTimeoutError,
)
from modbus_connection.pymodbus import ModbusConnection

from .const import ModbusExceptions
from .exceptions import (
    ModbusIllegalAddress,
    ModbusIllegalFunction,
    ModbusIllegalValue,
    ModbusIOError,
    ModbusReadError,
    ModbusWriteError,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class PollStats:
    """Diagnostics-only counters for one transport session."""

    reads: int = 0
    writes: int = 0
    connects: int = 0
    reconnects: int = 0
    recycles: int = 0
    connection_losses: int = 0
    last_error: str | None = field(default=None, repr=False)


class ModbusReadResult:
    """Wrap a register list so callers keep the `.registers` interface."""

    __slots__ = ("registers",)

    def __init__(self, registers: list[int]) -> None:
        self.registers = registers


def _is_cancellation(exc: BaseException) -> bool:
    """Whether this failure is really a cancellation wearing a disguise.

    pymodbus catches the CancelledError raised into an in-flight request and
    reports ModbusIOException("Request cancelled outside library"), which the
    library then reports as a timeout. Left alone that would silently convert
    two very different things: an expired whole-poll deadline (which must
    reach the coordinator as TimeoutError to count against its retry limit)
    and an unload cancelling the task (which must actually cancel).
    """
    task = asyncio.current_task()
    if task is not None and task.cancelling() > 0:
        return True

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, asyncio.CancelledError):
            return True
        current = current.__cause__ or current.__context__

    return False


def _illegal_for(code: int | None):
    """Map a Modbus exception code to our exception type, if it is one."""
    return {
        ModbusExceptions.IllegalAddress: ModbusIllegalAddress,
        ModbusExceptions.IllegalFunction: ModbusIllegalFunction,
        ModbusExceptions.IllegalValue: ModbusIllegalValue,
    }.get(code)


class ModbusTransport:
    """A single serialized Modbus/TCP client session."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        timeout: int,
        retries: int = 0,
        reconnect_delay: float = 0,
        reconnect_delay_max: float = 0,
        connection_factory: Callable[..., ModbusConnection] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        # Accepted for call-site compatibility. modbus-connection issues each
        # request once and reconnects on demand, so these are no longer ours
        # to set; the enclosing deadlines already pre-empted pymodbus retries.
        self._retries = retries
        self._reconnect_delay = reconnect_delay
        self._reconnect_delay_max = reconnect_delay_max
        self._connection_factory = connection_factory

        self._connection: ModbusConnection | None = None
        self._generation = 0
        self._lock = asyncio.Lock()
        self._lock_holder: asyncio.Task | None = None
        self.stats = PollStats()

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def connected(self) -> bool:
        if self._connection is None:
            return False

        return self._connection.connected

    def _held_by_current_task(self) -> bool:
        return self._lock_holder is asyncio.current_task()

    # -- connection generations -------------------------------------------

    def _build_connection(self) -> ModbusConnection:
        _LOGGER.debug(
            f"New ModbusConnection generation {self._generation} "
            f"for {self._host}:{self._port} timeout={self._timeout}"
        )
        if self._connection_factory is not None:
            connection = self._connection_factory(
                host=self._host, port=self._port, timeout=self._timeout
            )
        else:
            connection = ModbusConnection(
                ModbusTcpParams(host=self._host, port=self._port),
                timeout=self._timeout,
            )

        # Bind the callback to this generation: a dead connection dropping
        # its socket must not touch counters that now belong to a live one.
        generation = self._generation
        connection.on_connection_lost(lambda: self._note_connection_lost(generation))
        self.stats.connects += 1
        return connection

    def _note_connection_lost(self, generation: int) -> None:
        if generation != self._generation:
            return

        self.stats.connection_losses += 1
        _LOGGER.debug(f"Connection lost to {self._host}:{self._port}")

    def _current(self) -> ModbusConnection:
        if self._connection is None:
            self._connection = self._build_connection()

        return self._connection

    async def _recycle_unlocked(self) -> None:
        """Retire this connection generation and build a fresh one on next use.

        The library's close() is permanent (it marks the connection closed
        for good), so a wedged socket cannot be reopened — only replaced.
        """
        connection, self._connection = self._connection, None
        self._generation += 1
        if connection is None:
            return

        self.stats.recycles += 1
        _LOGGER.debug(f"Recycling connection to {self._host}:{self._port}")
        try:
            await connection.close()
        except (ModbusError, OSError) as e:  # closing must not mask the cause
            _LOGGER.debug(f"Error closing retired connection: {e}")

    async def _shielded_recycle(self) -> None:
        """Retire the generation even though the caller is being cancelled.

        The state change is synchronous, so the generation is retired either
        way; the shield is what lets the socket close actually finish rather
        than being abandoned half-way by the same cancellation.
        """
        await asyncio.shield(self._recycle_unlocked())

    async def recycle(self) -> None:
        """Replace the connection, taking the lock unless already held."""
        if self._held_by_current_task():
            await self._recycle_unlocked()
            return
        async with self._lock:
            await self._recycle_unlocked()

    async def connect(self) -> None:
        """Establish the link, building a connection on first use."""
        if self._held_by_current_task():
            await self._connect_unlocked()
            return
        async with self._lock:
            await self._connect_unlocked()

    async def _connect_unlocked(self) -> None:
        # Counted on the attempt, not on every access while down, so one
        # outage is one reconnect in diagnostics rather than however many
        # calls happened to span it.
        reconnecting = self._connection is not None
        connection = self._current()
        if connection.connected:
            return

        if reconnecting:
            self.stats.reconnects += 1

        _LOGGER.debug(f"Connecting to {self._host}:{self._port} ...")
        try:
            await connection.connect()
        except (ModbusConnectionError, ModbusTimeoutError) as e:
            self.stats.last_error = f"connect: {type(e).__name__}"
            await self._recycle_unlocked()
            raise ModbusIOError(f"Connect to {self._host}:{self._port} failed: {e}")
        except asyncio.CancelledError:
            # The library shields its connect task, so cancelling the caller
            # does not cancel the connect: it can still succeed and leave a
            # live socket behind. These inverters accept one session, so the
            # generation is retired rather than leaked.
            self.stats.last_error = "connect: cancelled"
            await self._shielded_recycle()
            raise

    async def disconnect(self, clear_client: bool = False) -> None:
        """Close the session.

        Every close is a recycle now: the library cannot reopen a closed
        connection, so `clear_client` no longer distinguishes anything. It
        is kept so call sites read unchanged.
        """
        if self._held_by_current_task():
            await self._recycle_unlocked()
            return
        async with self._lock:
            await self._recycle_unlocked()

    # -- register i/o ------------------------------------------------------

    async def read_holding_registers_raw(
        self, unit: int, address: int, count: int
    ) -> ModbusReadResult:
        """Locked read; raises our exception types, never the library's."""
        if self._held_by_current_task():
            return await self._read_unlocked(unit, address, count)

        async with self._lock:
            self._lock_holder = asyncio.current_task()
            try:
                return await self._read_unlocked(unit, address, count)
            finally:
                self._lock_holder = None

    async def _read_unlocked(
        self, unit: int, address: int, count: int
    ) -> ModbusReadResult:
        connection = self._current()
        self.stats.reads += 1
        try:
            registers = await connection.for_unit(unit).read_holding_registers(
                address, count
            )

        except asyncio.CancelledError:
            # The library auto-connects before every operation and shields that
            # connect, so a cancellation here can leave a live socket nobody
            # owns. CancelledError is a BaseException, so the ModbusError arm
            # below never sees it.
            self.stats.last_error = f"read unit {unit}: cancelled"
            await self._shielded_recycle()
            raise

        except ModbusExceptionError as e:
            # The device answered, just not with data. Sanitized: type and
            # code only, never payload or host.
            self.stats.last_error = (
                f"read unit {unit}: ExceptionResponse(code={e.exception_code})"
            )
            illegal = _illegal_for(e.exception_code)
            if illegal is not None:
                raise illegal(e)
            raise ModbusReadError(e)

        except ModbusError as e:
            # Anything that is not an exception PDU: no usable answer, a
            # malformed frame, or a cancellation pymodbus swallowed. The socket
            # may be half-open or desynchronised either way, so the generation
            # is retired rather than reused. Note only a real exception PDU is
            # evidence a device is answering — the ID scanner keys on that.
            self.stats.last_error = f"read unit {unit}: {type(e).__name__}"
            await self._recycle_unlocked()
            if _is_cancellation(e):
                raise asyncio.CancelledError from e
            # Deliberately ModbusIOError, never TimeoutError — see the helper.
            raise ModbusIOError(e)

        return ModbusReadResult(registers)

    async def write_registers_raw(self, unit: int, address: int, payload: list[int]):
        """Locked write (function 16); raises our exception types.

        A lost response is reported as an unknown outcome and never retried:
        the write may well have been applied, and these are power-control
        registers.
        """
        async with self._lock:
            self.stats.writes += 1
            connection = self._current()
            try:
                await connection.for_unit(unit).write_registers(address, payload)

            except asyncio.CancelledError:
                # The frame may already be on the wire; the session must not be
                # left open regardless. Callers treat this as uncertain.
                self.stats.last_error = f"write unit {unit}: cancelled"
                await self._shielded_recycle()
                raise

            except ModbusExceptionError as e:
                self.stats.last_error = (
                    f"write unit {unit}: ExceptionResponse(code={e.exception_code})"
                )
                illegal = _illegal_for(e.exception_code)
                if illegal is not None:
                    raise illegal(e)
                raise ModbusWriteError(e)

            except ModbusError as e:
                # Only an exception PDU proves the device refused the frame and
                # applied nothing. Every other failure — lost response, garbled
                # frame, swallowed cancellation — can happen after function 16
                # went out, so the outcome is unknown and the caller must never
                # re-send on its own.
                self.stats.last_error = f"write unit {unit}: {type(e).__name__}"
                await self._recycle_unlocked()
                if _is_cancellation(e):
                    raise asyncio.CancelledError from e
                raise ModbusIOError(
                    f"No confirmed response to write at {address} on unit "
                    f"{unit}; the write may or may not have been applied: {e}"
                )

    def hold_session(self) -> _SessionHold:
        """Reserve the session for a batch of calls by the current task.

        Reads inside the batch take the reentrant fast path, so a device's
        whole read cycle is one uninterrupted session use (writes queue
        behind it).
        """
        return _SessionHold(self)


class _SessionHold:
    def __init__(self, transport: ModbusTransport) -> None:
        self._transport = transport

    async def __aenter__(self) -> ModbusTransport:
        await self._transport._lock.acquire()
        self._transport._lock_holder = asyncio.current_task()
        return self._transport

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._transport._lock_holder = None
        self._transport._lock.release()
