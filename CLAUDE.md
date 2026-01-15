# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **forked repository** of a Home Assistant custom integration (`solaredge_modbus_multi`) that provides Modbus/TCP local polling for SolarEdge inverters. It supports multiple inverters (1-32), meters (up to 3 per inverter), and batteries (up to 3 per inverter) via the SunSpec protocol.

### Repository Structure
- **Origin**: `git@github.com:cohenam/solaredge-modbus-multi.git` (this fork)
- **Upstream**: `git@github.com:WillCodeForCats/solaredge-modbus-multi.git` (original)

### Syncing with Upstream
```bash
git fetch upstream
git merge upstream/main
# Or rebase: git rebase upstream/main
```

## Development Commands

### Linting
The project uses Ruff for linting and formatting:
- **ruff**: line-length=88 (see `pyproject.toml`)
- Ruff handles linting (E, W, F, I, B rules) and formatting
- Run `ruff check custom_components/` and `ruff format custom_components/`

### Dependencies
- Python 3.12+
- pymodbus>=3.8.3
- Home Assistant 2025.2.0+

No build step required - this is a Home Assistant integration installed via HACS or manually copying to `config/custom_components/`.

## Architecture

### Core Components

**`hub.py`** - Central Modbus communication layer
- `SolarEdgeModbusMultiHub`: Main hub class managing all Modbus TCP connections
- Contains device classes: `SolarEdgeInverter`, `SolarEdgeMeter`, `SolarEdgeBattery`
- Handles connection pooling, retries, and async read/write operations
- Custom exception hierarchy: `HubInitFailed`, `DataUpdateFailed`, `ModbusReadError`, etc.

**`__init__.py`** - Integration entry point
- `SolarEdgeCoordinator`: DataUpdateCoordinator with retry logic for Modbus polling
- Config entry setup/migration (version 1 → 2.x migration supported)
- Advanced YAML configuration schema for retry/modbus tuning

**`const.py`** - All constants and enums
- Modbus register base addresses (`BATTERY_REG_BASE`, `METER_REG_BASE`)
- SunSpec protocol values and status codes
- Configuration defaults (`ConfDefaultInt`, `ConfDefaultFlag`, `ConfDefaultStr`)
- Device status mappings (`DEVICE_STATUS`, `VENDOR_STATUS`, `BATTERY_STATUS`)

**`sensor.py`** - All sensor entity definitions
- Inverter sensors: AC/DC power, voltage, current, frequency, energy, temperature
- Meter sensors: Power, energy, voltage per phase
- Battery sensors: State of charge, power, temperature, health

**`config_flow.py`** - UI configuration flow for Home Assistant

**`helpers.py`** - Utility functions for Modbus data conversion

### Entity Platform Files
- `binary_sensor.py` - Binary sensor entities
- `button.py` - Button entities (controls)
- `number.py` - Number entities (adjustable values)
- `select.py` - Select entities (dropdown controls)
- `switch.py` - Switch entities (on/off controls)

### Data Flow
1. `SolarEdgeCoordinator` triggers periodic updates (configurable scan interval)
2. `SolarEdgeModbusMultiHub` opens Modbus TCP connection
3. Hub reads registers from each inverter, then discovers/reads meters and batteries
4. Data stored in device objects, coordinator notifies entities
5. Entities read from their device objects and update HA state

### Key Configuration Options
- `device_list`: List of Modbus unit IDs for inverters
- `detect_meters`/`detect_batteries`: Auto-discovery toggles
- `keep_modbus_open`: Persistent connection vs. connect-per-poll
- `advanced_power_control`, `adv_storage_control`, `adv_site_limit_control`: Enable advanced control entities

### Modbus Protocol Notes
- Uses SunSpec protocol over Modbus TCP (default port 1502)
- Inverter registers start at 40000+
- Meter registers: 40121, 40295, 40469 (for meters 1-3)
- Battery registers: 57600, 57856, 58368 (for batteries 1-3)
- Scale factors (SF) used for converting raw register values
