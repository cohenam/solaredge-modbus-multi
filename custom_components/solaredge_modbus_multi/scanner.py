"""Device ID scanner for SolarEdge Modbus Multi.

Scans Modbus device IDs by reading the SunSpec common-block header
(40000, 9 registers) and matching the SolarEdge signature: "SunS",
DID 1, length 65, manufacturer "SolarEdge".

Built on ModbusTransport (pymodbus) rather than a raw socket, so MBAP
framing is length-aware (fragmented TCP responses are reassembled),
transaction/unit IDs are matched by the transaction layer (a stale frame
becomes a timeout, not a phantom "other device"), and exception PDUs are
classified as a responding non-inverter device.

Original raw-socket approach based on work by thargy:
https://github.com/thargy/modbus-scanner

The classification API is unchanged: FOUND_INV for a SolarEdge inverter,
FOUND for any other responding Modbus device, NOT_FOUND for no response.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.exceptions import HomeAssistantError
from pymodbus.exceptions import ConnectionException, ModbusIOException

try:  # pymodbus 3.11.1+
    from pymodbus.pdu.pdu import ExceptionResponse
except ImportError:  # older pymodbus
    from pymodbus.pdu import ExceptionResponse

from .modbus_transport import ModbusTransport

_LOGGER = logging.getLogger(__name__)

SUNSPEC_COMMON_ADDRESS = 40000
SUNSPEC_COMMON_HEADER_COUNT = 9
SUNSPEC_ID = 0x53756E53  # "SunS"
SUNSPEC_COMMON_DID = 0x0001
SUNSPEC_COMMON_LENGTH = 65
SOLAREDGE_MANUFACTURER = "SolarEdge"


class SolarEdgeDeviceScanner:
    NOT_FOUND = 0
    FOUND = 1
    FOUND_INV = 2

    def __init__(
        self,
        host: str,
        port: int,
        connect_timeout: float = 5.0,
        scan_retries: int = 3,
        scan_timeout: float = 3.0,
    ):
        """Initialize the SolarEdge device scanner.

        Args:
            host: Target host address.
            port: Target port number.
            connect_timeout: Connection timeout in seconds.
            scan_retries: Number of retry attempts for failed scans.
            scan_timeout: Per-device response timeout in seconds.
        """
        self._connect_timeout = connect_timeout
        self._scan_retries = scan_retries
        self._scan_timeout = scan_timeout
        self._host = host
        self._port = port
        # A private session: retries=0 because this class does its own
        # retry loop, and no auto-reconnect (reconnect happens explicitly).
        self._transport = ModbusTransport(
            host=host,
            port=port,
            timeout=scan_timeout,
            retries=0,
            reconnect_delay=0,
            reconnect_delay_max=0,
        )

        self.inverters = []

    async def scan_list(
        self,
        device_list: list[int],
        progress_callback: callable = None,
    ) -> list[int]:
        """Scan a list of device IDs for SolarEdge inverters.

        Args:
            device_list: List of Modbus device IDs to scan.
            progress_callback: Optional callback to report progress.
                               Called with (scanned_count, total_count).

        Returns:
            List of device IDs that are SolarEdge inverters.
        """
        total = len(device_list)
        scanned = 0

        if progress_callback:
            await progress_callback(scanned, total)

        for device_id in device_list:
            _LOGGER.debug(f"Calling scan_device_id on device_id={device_id}")
            result = await self.scan_device_id(device_id, self._scan_timeout)
            if result == self.FOUND_INV:
                self.inverters.append(device_id)

            scanned += 1
            if progress_callback:
                _LOGGER.debug(f"scan_list progress: {scanned} of {total}")
                await progress_callback(scanned, total)

        return self.inverters

    async def check_list(self, device_list: list[int]) -> dict[str, list[int]]:
        """Check a list of device IDs and categorize the results.

        Args:
            device_list: List of Modbus device IDs to validate.

        Returns:
            Dictionary with three lists:
            - "inverters": Device IDs that are SolarEdge inverters
            - "other_devices": Device IDs that responded but aren't SolarEdge inverters
            - "no_response": Device IDs that didn't respond or timed out
        """
        inverters = []
        other_devices = []
        no_response = []

        for device_id in device_list:
            result = await self.scan_device_id(device_id, self._scan_timeout)
            if result == self.FOUND_INV:
                inverters.append(device_id)
            elif result == self.FOUND:
                other_devices.append(device_id)
            else:
                no_response.append(device_id)

        return {
            "inverters": inverters,
            "other_devices": other_devices,
            "no_response": no_response,
        }

    async def connect(self) -> None:
        """Establish the Modbus/TCP session."""
        attempt = 1

        while not self._transport.connected and attempt <= self._scan_retries:
            try:
                async with asyncio.timeout(self._connect_timeout):
                    await self._transport.connect()
            except TimeoutError:
                _LOGGER.warning(
                    f"Timeout occurred while connecting to {self._host}:{self._port}"
                )
            except OSError as e:
                _LOGGER.warning(
                    f"Network error connecting to {self._host}:{self._port}: {e}"
                )

            if not self._transport.connected:
                await self._transport.disconnect(clear_client=True)
                attempt += 1
                await asyncio.sleep(1.0)

        if not self._transport.connected:
            raise HomeAssistantError(
                f"Unable to connect to {self._host}:{self._port} "
                f"after {attempt - 1} attempts."
            )

    async def disconnect(self) -> None:
        """Close the Modbus/TCP session."""
        await self._transport.disconnect(clear_client=True)

    @staticmethod
    def _is_solaredge_signature(registers: list[int]) -> bool:
        """Match the SunSpec common-block header of a SolarEdge inverter."""
        sunspec_id = (registers[0] << 16) | registers[1]
        manufacturer = "".join(
            chr(reg >> 8) + chr(reg & 0xFF) for reg in registers[4:9]
        )
        return (
            sunspec_id == SUNSPEC_ID
            and registers[2] == SUNSPEC_COMMON_DID
            and registers[3] == SUNSPEC_COMMON_LENGTH
            and manufacturer.startswith(SOLAREDGE_MANUFACTURER)
        )

    async def scan_device_id(self, device_id: int, timeout: float = 5.0) -> int:
        """Scan a specific Modbus device ID for a SolarEdge inverter.

        Args:
            device_id: The Modbus device ID to scan (1-247).
            timeout: Maximum time in seconds to wait for a response.

        Returns:
            FOUND_INV (2) if a SolarEdge inverter was detected.
            FOUND (1) if a non-inverter Modbus device responded.
            NOT_FOUND (0) if no valid response was received.
        """
        attempt = 1

        # Response waiting is governed by the pymodbus request timeout
        # (scan_timeout): a silent or mismatched (stale txn / wrong unit)
        # response surfaces as ModbusIOException after `timeout` seconds.
        # After any failure the client is dropped and rebuilt, so every
        # attempt starts from a clean connection state.
        while attempt <= self._scan_retries:
            if not self._transport.connected:
                try:
                    async with asyncio.timeout(self._connect_timeout):
                        await self._transport.connect()
                except (TimeoutError, OSError, ConnectionException):
                    await self._transport.disconnect(clear_client=True)
                    attempt += 1
                    continue

                if not self._transport.connected:
                    await self._transport.disconnect(clear_client=True)
                    attempt += 1
                    continue

            try:
                _LOGGER.debug(f"Scanning ID: {device_id} ...")
                result = await self._transport.read_holding_registers_raw(
                    device_id, SUNSPEC_COMMON_ADDRESS, SUNSPEC_COMMON_HEADER_COUNT
                )

            except (ConnectionException, ModbusIOException, OSError) as e:
                _LOGGER.debug(f" FAILED: {e}")
                await self._transport.disconnect(clear_client=True)
                attempt += 1
                continue

            if isinstance(result, ModbusIOException):
                _LOGGER.debug(f" No response from ID {device_id}: {result}")
                await self._transport.disconnect(clear_client=True)
                attempt += 1
                continue

            if isinstance(result, ExceptionResponse) or result.isError():
                # A device answered (even if with a Modbus exception), so
                # something real is at this ID — just not a usable inverter.
                _LOGGER.debug(f" ID {device_id} answered with error: {result}")
                return self.FOUND

            if len(result.registers) != SUNSPEC_COMMON_HEADER_COUNT:
                _LOGGER.debug(
                    f" ID {device_id} short response: {len(result.registers)} regs"
                )
                return self.FOUND

            if self._is_solaredge_signature(result.registers):
                _LOGGER.debug(f" {device_id} is INVERTER")
                return self.FOUND_INV

            _LOGGER.debug(
                f"Scanned device {device_id} did not match signature: "
                f"{' '.join(format(reg, '04x') for reg in result.registers)}"
            )
            return self.FOUND

        _LOGGER.debug(f" No device found at ID {device_id}")
        return self.NOT_FOUND
