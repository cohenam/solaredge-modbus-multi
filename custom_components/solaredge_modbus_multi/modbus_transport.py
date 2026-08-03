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
    ModbusProtocolError,
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
        elif not self._connection.connected:
            self.stats.reconnects += 1

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
        connection = self._current()
        if connection.connected:
            return

        _LOGGER.debug(f"Connecting to {self._host}:{self._port} ...")
        try:
            await connection.connect()
        except (ModbusConnectionError, ModbusTimeoutError) as e:
            self.stats.last_error = f"connect: {type(e).__name__}"
            await self._recycle_unlocked()
            raise ModbusIOError(f"Connect to {self._host}:{self._port} failed: {e}")

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

        except (ModbusTimeoutError, ModbusConnectionError, ModbusProtocolError) as e:
            # No usable answer: the socket may be half-open or desynchronised,
            # so the generation is retired rather than reused. Deliberately
            # ModbusIOError and not TimeoutError — see the module docstring.
            self.stats.last_error = f"read unit {unit}: {type(e).__name__}"
            await self._recycle_unlocked()
            raise ModbusIOError(e)

        except ModbusError as e:
            # A malformed or unrecognised answer, not an exception PDU. Treat
            # it as a transport failure so callers can retry — only a real
            # exception PDU is evidence that a device is there and answering,
            # which is what the ID scanner keys its classification on.
            self.stats.last_error = f"read unit {unit}: {type(e).__name__}"
            await self._recycle_unlocked()
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

            except ModbusExceptionError as e:
                self.stats.last_error = (
                    f"write unit {unit}: ExceptionResponse(code={e.exception_code})"
                )
                illegal = _illegal_for(e.exception_code)
                if illegal is not None:
                    raise illegal(e)
                raise ModbusWriteError(e)

            except (
                ModbusTimeoutError,
                ModbusConnectionError,
                ModbusProtocolError,
            ) as e:
                self.stats.last_error = f"write unit {unit}: {type(e).__name__}"
                await self._recycle_unlocked()
                raise ModbusIOError(
                    f"No response to write at {address} on unit {unit}; "
                    f"the write may or may not have been applied: {e}"
                )

            except ModbusError as e:
                self.stats.last_error = f"write unit {unit}: {type(e).__name__}"
                raise ModbusWriteError(e)

    def set_unit_spacing(self, unit: int, seconds: float) -> None:
        """Set (or clear, with 0) a minimum gap between one unit's requests."""
        self._current().for_unit(unit).set_message_spacing(seconds)

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
