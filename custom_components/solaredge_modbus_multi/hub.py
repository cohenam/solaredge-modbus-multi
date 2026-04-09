from __future__ import annotations

import asyncio
import importlib.metadata
import inspect
import logging

from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusIOException

try:
    # for pymodbus 3.11.1 and newer
    from pymodbus.pdu.pdu import ExceptionResponse
except ImportError:
    # or backwards compatibility
    from pymodbus.pdu import ExceptionResponse

from .const import (
    BATTERY_REG_BASE,
    DOMAIN,
    METER_REG_BASE,
    PYMODBUS_REQUIRED_VERSION,
    ConfDefaultFlag,
    ConfDefaultInt,
    ConfDefaultStr,
    ConfName,
    ModbusDefaults,
    ModbusExceptions,
    RetrySettings,
    SolarEdgeTimeouts,
)

_LOGGER = logging.getLogger(__name__)
pymodbus_version = importlib.metadata.version("pymodbus")


class SolarEdgeException(Exception):
    """Base class for other exceptions"""

    pass


class HubInitFailed(SolarEdgeException):
    """Raised when an error happens during init"""

    pass


class DeviceInitFailed(SolarEdgeException):
    """Raised when a device can't be initialized"""

    pass


class ModbusReadError(SolarEdgeException):
    """Raised when a modbus read fails (generic)"""

    pass


class ModbusIllegalFunction(SolarEdgeException):
    """Raised when a modbus address is invalid"""

    pass


class ModbusIllegalAddress(SolarEdgeException):
    """Raised when a modbus address is invalid"""

    pass


class ModbusIllegalValue(SolarEdgeException):
    """Raised when a modbus address is invalid"""

    pass


class ModbusIOError(SolarEdgeException):
    """Raised when a modbus IO error occurs"""

    pass


class ModbusWriteError(SolarEdgeException):
    """Raised when a modbus write fails (generic)"""

    pass


class DataUpdateFailed(SolarEdgeException):
    """Raised when an update cycle fails"""

    pass


class DeviceInvalid(SolarEdgeException):
    """Raised when a device is not usable or invalid"""

    pass


class SolarEdgeModbusMultiHub:
    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        entry_data,
        entry_options,
    ):
        """Initialize the Modbus hub."""
        self._hass = hass
        self._yaml_config = hass.data[DOMAIN]["yaml"]
        self._name = entry_data[CONF_NAME]
        self._host = entry_data[CONF_HOST]
        self._port = entry_data[CONF_PORT]
        self._entry_id = entry_id
        self._inverter_list = entry_data.get(
            ConfName.DEVICE_LIST, [ConfDefaultStr.DEVICE_LIST]
        )
        self._detect_meters = entry_options.get(
            ConfName.DETECT_METERS, bool(ConfDefaultFlag.DETECT_METERS)
        )
        self._detect_batteries = entry_options.get(
            ConfName.DETECT_BATTERIES, bool(ConfDefaultFlag.DETECT_BATTERIES)
        )
        self._detect_extras = entry_options.get(
            ConfName.DETECT_EXTRAS, bool(ConfDefaultFlag.DETECT_EXTRAS)
        )
        self._keep_modbus_open = entry_options.get(
            ConfName.KEEP_MODBUS_OPEN, bool(ConfDefaultFlag.KEEP_MODBUS_OPEN)
        )
        self._adv_storage_control = entry_options.get(
            ConfName.ADV_STORAGE_CONTROL, bool(ConfDefaultFlag.ADV_STORAGE_CONTROL)
        )
        self._adv_site_limit_control = entry_options.get(
            ConfName.ADV_SITE_LIMIT_CONTROL,
            bool(ConfDefaultFlag.ADV_SITE_LIMIT_CONTROL),
        )
        self._allow_battery_energy_reset = entry_options.get(
            ConfName.ALLOW_BATTERY_ENERGY_RESET,
            bool(ConfDefaultFlag.ALLOW_BATTERY_ENERGY_RESET),
        )
        self._sleep_after_write = entry_options.get(
            ConfName.SLEEP_AFTER_WRITE, ConfDefaultInt.SLEEP_AFTER_WRITE
        )
        self._battery_rating_adjust = entry_options.get(
            ConfName.BATTERY_RATING_ADJUST, ConfDefaultInt.BATTERY_RATING_ADJUST
        )
        self._battery_energy_reset_cycles = entry_options.get(
            ConfName.BATTERY_ENERGY_RESET_CYCLES,
            ConfDefaultInt.BATTERY_ENERGY_RESET_CYCLES,
        )
        self._retry_limit = self._yaml_config.get("retry", {}).get(
            "limit", RetrySettings.Limit
        )
        self._mb_reconnect_delay = self._yaml_config.get("modbus", {}).get(
            "reconnect_delay", ModbusDefaults.ReconnectDelay
        )
        self._mb_reconnect_delay_max = self._yaml_config.get("modbus", {}).get(
            "reconnect_delay_max", ModbusDefaults.ReconnectDelayMax
        )
        self._mb_timeout = self._yaml_config.get("modbus", {}).get(
            "timeout", ModbusDefaults.Timeout
        )
        self._mb_retries = self._yaml_config.get("modbus", {}).get(
            "retries", ModbusDefaults.Retries
        )
        self._id = entry_data[CONF_NAME].lower()
        self.inverters = []
        self.meters = []
        self.batteries = []
        self.inverter_common = {}
        self.mmppt_common = {}
        self.has_write = None

        self._initalized = False
        self._online = True
        self._timeout_counter = 0

        self._client = None
        self._modbus_lock = asyncio.Lock()
        self._lock_holder = None  # Track current task holding the lock
        self._use_device_id_param = None  # Cached signature check

        self._pymodbus_version = pymodbus_version

        _LOGGER.debug(
            (
                f"{DOMAIN} configuration: "
                f"inverter_list={self._inverter_list}, "
                f"detect_meters={self._detect_meters}, "
                f"detect_batteries={self._detect_batteries}, "
                f"detect_extras={self._detect_extras}, "
                f"keep_modbus_open={self._keep_modbus_open}, "
                f"adv_storage_control={self._adv_storage_control}, "
                f"adv_site_limit_control={self._adv_site_limit_control}, "
                f"allow_battery_energy_reset={self._allow_battery_energy_reset}, "
                f"sleep_after_write={self._sleep_after_write}, "
                f"battery_rating_adjust={self._battery_rating_adjust}, "
            ),
        )

        _LOGGER.debug(f"pymodbus version {self.pymodbus_version}")

    async def _discover_devices(
        self,
        inverter,
        device_cls,
        reg_bases: dict,
        device_list: list,
        label: str,
        allow_duplicate_serial: bool = False,
    ) -> None:
        """Discover meters or batteries for an inverter.

        Args:
            inverter: The parent inverter device.
            device_cls: Device class to instantiate (SolarEdgeMeter or SolarEdgeBattery).
            reg_bases: Dict of register base addresses keyed by device ID.
            device_list: List to append discovered devices to.
            label: Short label for logging (e.g. "M" for meter, "B" for battery).
            allow_duplicate_serial: If True, warn on duplicate serials but continue.
                If False, raise DeviceInvalid on duplicate serials.
        """
        for reg_id in reg_bases:
            try:
                _LOGGER.debug(
                    f"Looking for {label} I{inverter.inverter_unit_id}{label}{reg_id}"
                )
                device = device_cls(inverter.inverter_unit_id, reg_id, self)
                await device.init_device()

                for existing in device_list:
                    if device.serial == existing.serial:
                        if allow_duplicate_serial:
                            _LOGGER.warning(
                                f"Duplicate serial {device.serial} "
                                f"on I{inverter.inverter_unit_id}{label}{reg_id}"
                            )
                        else:
                            _LOGGER.warning(
                                f"Duplicate serial {device.serial} "
                                f"on I{inverter.inverter_unit_id}{label}{reg_id}"
                            )
                            raise DeviceInvalid(
                                f"Duplicate {label}{reg_id} serial {device.serial}"
                            )

                device.via_device = inverter.uid_base
                device_list.append(device)
                _LOGGER.debug(f"Found I{inverter.inverter_unit_id}{label}{reg_id}")

            except (ModbusReadError, TimeoutError) as e:
                await self.disconnect()
                raise HubInitFailed(f"{e}")

            except DeviceInvalid as e:
                _LOGGER.debug(f"I{inverter.inverter_unit_id}{label}{reg_id}: {e}")

    async def _async_init_solaredge(self) -> None:
        """Detect devices and load initial modbus data from inverters."""

        pymodbus_version_tuple = self._safe_version_tuple(self.pymodbus_version)
        required_version_tuple = self._safe_version_tuple(
            self.pymodbus_required_version
        )

        if pymodbus_version_tuple < required_version_tuple:
            raise HubInitFailed(
                f"pymodbus version must be at least {self.pymodbus_required_version}, "
                f"but {self.pymodbus_version} is installed. Please remove other custom "
                "integrations that depend on an older version of pymodbus and restart."
            )

        if not self.is_connected:
            ir.async_create_issue(
                self._hass,
                DOMAIN,
                "check_configuration",
                is_fixable=True,
                severity=ir.IssueSeverity.ERROR,
                translation_key="check_configuration",
                data={"entry_id": self._entry_id},
            )
            raise HubInitFailed(
                f"Modbus/TCP connect to {self.hub_host}:{self.hub_port} failed."
            )

        if self.option_storage_control:
            _LOGGER.warning(
                (
                    "Power Control Options: Storage Control is enabled. "
                    "Use at your own risk! "
                    "Adjustable parameters in Modbus registers are intended for "
                    "long-term storage. Periodic changes may damage the flash memory."
                ),
            )

        if self.option_site_limit_control:
            _LOGGER.warning(
                (
                    "Power Control Options: Site Limit Control is enabled. "
                    "Use at your own risk! "
                    "Adjustable parameters in Modbus registers are intended for "
                    "long-term storage. Periodic changes may damage the flash memory."
                ),
            )

        for inverter_unit_id in self._inverter_list:
            try:
                _LOGGER.debug(
                    f"Looking for inverter at {self.hub_host} ID {inverter_unit_id}"
                )
                new_inverter = SolarEdgeInverter(inverter_unit_id, self)
                await new_inverter.init_device()
                self.inverters.append(new_inverter)

            except (ModbusReadError, TimeoutError) as e:
                await self.disconnect()
                raise HubInitFailed(f"{e}")

            except DeviceInvalid as e:
                # Inverters are mandatory
                _LOGGER.error(f"Inverter at {self.hub_host} ID {inverter_unit_id}: {e}")
                raise HubInitFailed(f"{e}")

            if self._detect_meters:
                await self._discover_devices(
                    inverter=new_inverter,
                    device_cls=SolarEdgeMeter,
                    reg_bases=METER_REG_BASE,
                    device_list=self.meters,
                    label="M",
                    allow_duplicate_serial=True,
                )

            if self._detect_batteries:
                await self._discover_devices(
                    inverter=new_inverter,
                    device_cls=SolarEdgeBattery,
                    reg_bases=BATTERY_REG_BASE,
                    device_list=self.batteries,
                    label="B",
                    allow_duplicate_serial=False,
                )

        try:
            # Read all devices sequentially with batch lock per device
            for inv in self.inverters:
                await self._poll_device_with_lock(inv)
            for meter in self.meters:
                await self._poll_device_with_lock(meter)
            for bat in self.batteries:
                await self._poll_device_with_lock(bat)

        except (
            ModbusReadError,
            DeviceInvalid,
            ConnectionException,
            ModbusIOException,
            TimeoutError,
        ) as e:
            await self.disconnect()
            if isinstance(e, ModbusReadError):
                raise HubInitFailed(f"Read error: {e}")
            if isinstance(e, DeviceInvalid):
                raise HubInitFailed(f"Invalid device: {e}")
            if isinstance(e, ConnectionException):
                raise HubInitFailed(f"Connection failed: {e}")
            if isinstance(e, ModbusIOException):
                raise HubInitFailed(f"Modbus error: {e}")
            raise HubInitFailed(f"Timeout error: {e}")

        self.initalized = True

    async def async_refresh_modbus_data(self) -> bool:
        """Refresh modbus data from inverters."""

        if not self.is_connected:
            await self.connect()

        if not self.initalized:
            try:
                async with asyncio.timeout(self.coordinator_timeout):
                    await self._async_init_solaredge()

            except (ConnectionException, ModbusIOException, TimeoutError) as e:
                await self.disconnect()
                ir.async_create_issue(
                    self._hass,
                    DOMAIN,
                    "check_configuration",
                    is_fixable=True,
                    severity=ir.IssueSeverity.ERROR,
                    translation_key="check_configuration",
                    data={"entry_id": self._entry_id},
                )
                raise HubInitFailed(f"Setup failed: {e}")

            ir.async_delete_issue(self._hass, DOMAIN, "check_configuration")

            if not self.keep_modbus_open:
                await self.disconnect()

            return True

        if not self.is_connected:
            self.online = False
            ir.async_create_issue(
                self._hass,
                DOMAIN,
                "check_configuration",
                is_fixable=True,
                severity=ir.IssueSeverity.ERROR,
                translation_key="check_configuration",
                data={"entry_id": self._entry_id},
            )
            raise DataUpdateFailed(
                f"Modbus/TCP connect to {self.hub_host}:{self.hub_port} failed."
            )

        if not self.online:
            ir.async_delete_issue(self._hass, DOMAIN, "check_configuration")

        self.online = True

        try:
            async with asyncio.timeout(self.coordinator_timeout):
                # Read all devices sequentially with batch lock per device
                for inv in self.inverters:
                    await self._poll_device_with_lock(inv)
                for meter in self.meters:
                    await self._poll_device_with_lock(meter)
                for bat in self.batteries:
                    await self._poll_device_with_lock(bat)

        except (
            ModbusReadError,
            DeviceInvalid,
            ConnectionException,
            ModbusIOException,
        ) as e:
            await self.disconnect()
            if isinstance(e, ModbusReadError):
                raise DataUpdateFailed(f"Update failed: {e}")
            if isinstance(e, DeviceInvalid):
                raise DataUpdateFailed(f"Invalid device: {e}")
            if isinstance(e, ConnectionException):
                raise DataUpdateFailed(f"Connection failed: {e}")
            raise DataUpdateFailed(f"Modbus error: {e}")

        except TimeoutError as e:
            await self.disconnect(clear_client=True)
            self._timeout_counter += 1

            _LOGGER.debug(
                f"Refresh timeout {self._timeout_counter} limit {self._retry_limit}"
            )

            if self._timeout_counter >= self._retry_limit:
                self._timeout_counter = 0
                raise TimeoutError

            raise DataUpdateFailed(f"Timeout error: {e}")

        if self._timeout_counter > 0:
            _LOGGER.debug(
                f"Timeout count {self._timeout_counter} limit {self._retry_limit}"
            )
            self._timeout_counter = 0

        if not self.keep_modbus_open:
            await self.disconnect()

        return True

    async def _connect_unlocked(self) -> None:
        """Connect to inverter (internal, caller must hold _modbus_lock)."""
        if self._client is None:
            _LOGGER.debug(
                "New AsyncModbusTcpClient: "
                f"reconnect_delay={self._mb_reconnect_delay} "
                f"reconnect_delay_max={self._mb_reconnect_delay_max} "
                f"timeout={self._mb_timeout} "
                f"retries={self._mb_retries}"
            )
            self._client = AsyncModbusTcpClient(
                host=self._host,
                port=self._port,
                reconnect_delay=self._mb_reconnect_delay,
                reconnect_delay_max=self._mb_reconnect_delay_max,
                timeout=self._mb_timeout,
                retries=self._mb_retries,
            )
            # Cache signature check once
            sig = inspect.signature(self._client.read_holding_registers)
            self._use_device_id_param = "device_id" in sig.parameters

        _LOGGER.debug((f"Connecting to {self._host}:{self._port} ..."))
        await self._client.connect()

    async def connect(self) -> None:
        """Connect to inverter."""
        if self._lock_holder is asyncio.current_task():
            await self._connect_unlocked()
            return
        async with self._modbus_lock:
            await self._connect_unlocked()

    def _disconnect_unlocked(self, clear_client: bool = False) -> None:
        """Disconnect from inverter (internal, caller must hold _modbus_lock)."""
        if self._client is not None:
            _LOGGER.debug(
                (
                    f"Disconnecting from {self._host}:{self._port} "
                    f"(clear_client={clear_client})."
                )
            )
            self._client.close()

            if clear_client:
                self._client = None

    async def disconnect(self, clear_client: bool = False) -> None:
        """Disconnect from inverter."""
        if self._lock_holder is asyncio.current_task():
            self._disconnect_unlocked(clear_client)
            return
        async with self._modbus_lock:
            self._disconnect_unlocked(clear_client)

    async def shutdown(self) -> None:
        """Shut down the hub and disconnect."""

        self.online = False
        await self.disconnect(clear_client=True)

    async def _read_registers_unlocked(self, unit, address, rcount):
        """Read modbus registers (internal, caller must hold _modbus_lock)."""

        _LOGGER.debug(
            f"I{unit}: modbus_read_holding_registers address={address} count={rcount}"
        )

        if self._use_device_id_param:
            result = await self._client.read_holding_registers(
                address=address, count=rcount, device_id=unit
            )
        else:
            result = await self._client.read_holding_registers(
                address=address, count=rcount, slave=unit
            )

        _LOGGER.debug(f"I{unit}: result is error: {result.isError()} ")

        if result.isError():
            _LOGGER.debug(f"I{unit}: error result: {type(result)} ")

            if type(result) is ModbusIOException:
                raise ModbusIOError(result)

            if type(result) is ExceptionResponse:
                if result.exception_code == ModbusExceptions.IllegalAddress:
                    _LOGGER.debug(f"I{unit} Read IllegalAddress: {result}")
                    raise ModbusIllegalAddress(result)

                if result.exception_code == ModbusExceptions.IllegalFunction:
                    _LOGGER.debug(f"I{unit} Read IllegalFunction: {result}")
                    raise ModbusIllegalFunction(result)

                if result.exception_code == ModbusExceptions.IllegalValue:
                    _LOGGER.debug(f"I{unit} Read IllegalValue: {result}")
                    raise ModbusIllegalValue(result)

            raise ModbusReadError(result)

        _LOGGER.debug(
            f"I{unit}: Registers received={len(result.registers)} "
            f"requested={rcount} address={address} "
            f"result={result}"
        )

        if len(result.registers) != rcount:
            raise ModbusReadError(
                f"I{unit}: Registers received != requested : "
                f"{len(result.registers)} != {rcount} at {address}"
            )

        return result

    async def modbus_read_holding_registers(self, unit, address, rcount):
        """Read modbus registers from inverter."""

        if self._lock_holder is asyncio.current_task():
            return await self._read_registers_unlocked(unit, address, rcount)

        async with self._modbus_lock:
            self._lock_holder = asyncio.current_task()
            try:
                return await self._read_registers_unlocked(unit, address, rcount)
            finally:
                self._lock_holder = None

    async def _poll_device_with_lock(self, device) -> None:
        """Poll a single device while holding the modbus lock.

        Holds the lock for the entire device read cycle, ensuring all
        registers for one unit_id are read atomically before moving to
        the next device. This prevents Modbus transaction ID confusion
        when SolarEdge firmware returns incorrect unit_id in responses.
        """
        async with self._modbus_lock:
            self._lock_holder = asyncio.current_task()
            try:
                await device.read_modbus_data()
            finally:
                self._lock_holder = None

    async def write_registers(self, unit: int, address: int, payload) -> None:
        """Write modbus registers to inverter."""

        try:
            async with self._modbus_lock:
                if not self.is_connected:
                    await self._connect_unlocked()

                if self._use_device_id_param:
                    result = await self._client.write_registers(
                        address=address,
                        values=payload,
                        device_id=unit,
                    )
                else:
                    result = await self._client.write_registers(
                        address=address,
                        values=payload,
                        slave=unit,
                    )

            self.has_write = address

            if self.sleep_after_write > 0:
                _LOGGER.debug(
                    f"Sleep {self.sleep_after_write} seconds after write {address}."
                )
                await asyncio.sleep(self.sleep_after_write)

            self.has_write = None
            _LOGGER.debug(f"Finished with write {address}.")

        except ModbusIOException as e:
            await self.disconnect()

            raise HomeAssistantError(
                f"Error sending command to inverter ID {unit}: {e}."
            )

        except ConnectionException as e:
            await self.disconnect()

            _LOGGER.error(f"Connection failed: {e}")
            raise HomeAssistantError(f"Connection to inverter ID {unit} failed.")

        if result.isError():
            if type(result) is ModbusIOException:
                await self.disconnect()
                _LOGGER.error(f"Write failed: No response from inverter ID {unit}.")
                raise HomeAssistantError(f"No response from inverter ID {unit}.")

            if type(result) is ExceptionResponse:
                if result.exception_code == ModbusExceptions.IllegalAddress:
                    _LOGGER.debug(f"Unit {unit} Write IllegalAddress: {result}")
                    raise HomeAssistantError(
                        f"Address not supported at device at ID {unit}."
                    )

                if result.exception_code == ModbusExceptions.IllegalFunction:
                    _LOGGER.debug(f"Unit {unit} Write IllegalFunction: {result}")
                    raise HomeAssistantError(
                        f"Function not supported by device at ID {unit}."
                    )

                if result.exception_code == ModbusExceptions.IllegalValue:
                    _LOGGER.debug(f"Unit {unit} Write IllegalValue: {result}")
                    raise HomeAssistantError(f"Value invalid for device at ID {unit}.")

            await self.disconnect()
            raise ModbusWriteError(result)

    @staticmethod
    def _safe_version_tuple(version_str: str) -> tuple[int, ...]:
        try:
            version_parts = version_str.split(".")
            version_tuple = tuple(int(part) for part in version_parts)
            return version_tuple
        except ValueError:
            raise ValueError(f"Invalid version string: {version_str}")

    @property
    def online(self):
        return self._online

    @online.setter
    def online(self, value: bool) -> None:
        self._online = bool(value)

    @property
    def initalized(self):
        return self._initalized

    @initalized.setter
    def initalized(self, value: bool) -> None:
        self._initalized = bool(value)

    @property
    def name(self):
        """Return the name of this hub."""
        return self._name

    @property
    def hub_id(self) -> str:
        """Return the ID of this hub."""
        return self._id

    @property
    def hub_host(self) -> str:
        """Return the modbus client host."""
        return self._host

    @property
    def hub_port(self) -> int:
        """Return the modbus client port."""
        return self._port

    @property
    def option_storage_control(self) -> bool:
        return self._adv_storage_control

    @property
    def option_site_limit_control(self) -> bool:
        return self._adv_site_limit_control

    @property
    def option_detect_extras(self) -> bool:
        return self._detect_extras

    @property
    def keep_modbus_open(self) -> bool:
        return self._keep_modbus_open

    @keep_modbus_open.setter
    def keep_modbus_open(self, value: bool) -> None:
        self._keep_modbus_open = bool(value)

        _LOGGER.debug(f"keep_modbus_open={self._keep_modbus_open}")

    @property
    def allow_battery_energy_reset(self) -> bool:
        return self._allow_battery_energy_reset

    @property
    def battery_rating_adjust(self) -> int:
        return (self._battery_rating_adjust + 100) / 100

    @property
    def battery_energy_reset_cycles(self) -> int:
        return self._battery_energy_reset_cycles

    @property
    def number_of_meters(self) -> int:
        return len(self.meters)

    @property
    def number_of_batteries(self) -> int:
        return len(self.batteries)

    @property
    def number_of_inverters(self) -> int:
        return len(self._inverter_list)

    @property
    def sleep_after_write(self) -> int:
        return self._sleep_after_write

    @property
    def pymodbus_required_version(self) -> str:
        return PYMODBUS_REQUIRED_VERSION

    @property
    def pymodbus_version(self) -> str:
        return self._pymodbus_version

    @property
    def coordinator_timeout(self) -> int:
        """Calculate coordinator timeout for sequential polling with batch lock.

        Devices are polled sequentially, each holding the lock for their
        entire read cycle. Timeout is the sum of per-device budgets:
        - SolarEdgeTimeouts.Inverter (3s) base for first inverter
        - SolarEdgeTimeouts.Device (0.5s) per additional device
        - SolarEdgeTimeouts.Init (0.8s) per device during discovery
        - SolarEdgeTimeouts.Read (2s) per extra read block
        """
        if not self.initalized:
            # Init: need time for discovery of all devices
            # Base timeout + increment per device being discovered
            this_timeout = SolarEdgeTimeouts.Inverter  # Base for first inverter
            this_timeout += SolarEdgeTimeouts.Init * self.number_of_inverters
            this_timeout += (SolarEdgeTimeouts.Device * 2) * 3  # max 3 meters
            this_timeout += (SolarEdgeTimeouts.Device * 2) * 2  # max 2 batteries
            if self.option_detect_extras:
                this_timeout += SolarEdgeTimeouts.Read * 3 * self.number_of_inverters

        else:
            # Normal polling: lock serializes but typical ops are fast
            # Use base + actual device counts
            this_timeout = SolarEdgeTimeouts.Inverter  # Base timeout
            # Add smaller increment for additional inverters (not full timeout each)
            this_timeout += SolarEdgeTimeouts.Device * (self.number_of_inverters - 1)
            this_timeout += SolarEdgeTimeouts.Device * self.number_of_meters
            this_timeout += SolarEdgeTimeouts.Device * self.number_of_batteries
            if self.option_detect_extras:
                this_timeout += SolarEdgeTimeouts.Read * 3

        this_timeout = this_timeout / 1000

        _LOGGER.debug(f"coordinator timeout is {this_timeout}")
        return this_timeout

    @property
    def is_connected(self) -> bool:
        """Check modbus client connection status."""
        if self._client is None:
            return False

        return self._client.connected


# Import device classes after hub class is defined to avoid circular imports.
# devices.py imports exception classes from this module at the top level.
from .devices import (  # noqa: E402
    SolarEdgeBattery,
    SolarEdgeInverter,
    SolarEdgeMeter,
    SolarEdgeMMPPTUnit,
)

__all__ = [
    "SolarEdgeModbusMultiHub",
    "SolarEdgeInverter",
    "SolarEdgeMeter",
    "SolarEdgeBattery",
    "SolarEdgeMMPPTUnit",
    "SolarEdgeException",
    "HubInitFailed",
    "DeviceInitFailed",
    "ModbusReadError",
    "ModbusIllegalFunction",
    "ModbusIllegalAddress",
    "ModbusIllegalValue",
    "ModbusIOError",
    "ModbusWriteError",
    "DataUpdateFailed",
    "DeviceInvalid",
]
