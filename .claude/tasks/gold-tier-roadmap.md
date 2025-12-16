# SolarEdge Modbus Multi - Gold Tier Roadmap

**Current Tier:** Bronze → Silver (with recent changes)
**Target:** Gold Tier
**Estimated Effort:** 2-4 weeks

---

## Gold Tier Requirements Checklist

| Requirement | Status | Priority | Notes |
|-------------|--------|----------|-------|
| Active code owner | ✅ Done | - | @WillCodeForCats |
| Automatic recovery from errors | ✅ Done | - | ConfigEntryNotReady added |
| No excessive logging | ✅ Done | - | DEBUG level for retries |
| Reconfigure flow | ❌ Missing | HIGH | Change host/port without re-adding |
| Translation support | ❌ Missing | HIGH | strings.json for multiple languages |
| Test coverage (80%+) | ❌ Partial | CRITICAL | Infrastructure added, need more tests |
| Non-technical documentation | ❌ Missing | MEDIUM | User-friendly setup guide |
| Entity descriptions | ❌ Missing | MEDIUM | Add entity_description to sensors |

---

## Phase 1: Complete Test Suite (Week 1)

### 1.1 Expand Test Coverage

**Files to create/expand:**

```
tests/
├── conftest.py          ✅ Created (expand fixtures)
├── test_config_flow.py  ✅ Created (expand scenarios)
├── test_init.py         ✅ Created (expand scenarios)
├── test_hub.py          ✅ Created (expand scenarios)
├── test_sensor.py       ❌ Create
├── test_coordinator.py  ❌ Create
├── test_binary_sensor.py ❌ Create
├── test_button.py       ❌ Create
├── test_number.py       ❌ Create
├── test_select.py       ❌ Create
├── test_switch.py       ❌ Create
└── fixtures/
    ├── __init__.py      ✅ Created
    └── mock_modbus_data.py ❌ Create (comprehensive mock data)
```

**Target:** 80%+ code coverage

### 1.2 Test Scenarios to Add

**Config Flow Tests:**
- [ ] User flow - successful setup
- [ ] User flow - connection timeout
- [ ] User flow - invalid device response
- [ ] User flow - duplicate entry prevention
- [ ] Options flow - change scan interval
- [ ] Options flow - enable/disable meters
- [ ] Options flow - enable/disable batteries
- [ ] Migration v1 → v2

**Hub Tests:**
- [ ] Connection success/failure
- [ ] Reconnection after timeout
- [ ] Parallel device polling
- [ ] Lock prevents race conditions
- [ ] Write operations
- [ ] Inverter detection
- [ ] Meter detection (1-3 meters)
- [ ] Battery detection (1-3 batteries)
- [ ] Register parsing accuracy

**Sensor Tests:**
- [ ] Inverter sensors created correctly
- [ ] Meter sensors created correctly
- [ ] Battery sensors created correctly
- [ ] Inverted power sensors (PR #928)
- [ ] Scale factor calculations
- [ ] Entity availability
- [ ] State updates from coordinator

**Coordinator Tests:**
- [ ] Successful data refresh
- [ ] Retry logic with backoff
- [ ] Write wait behavior
- [ ] Update failure handling

### 1.3 CI/CD Setup

**Create `.github/workflows/tests.yml`:**
```yaml
name: Tests

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ['3.12', '3.13']

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements_test.txt
          pip install -e .

      - name: Run tests with coverage
        run: |
          pytest tests/ \
            --cov=custom_components.solaredge_modbus_multi \
            --cov-report=xml \
            --cov-report=term-missing \
            --cov-fail-under=80

      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v4
        with:
          file: ./coverage.xml
```

---

## Phase 2: Reconfigure Flow (Week 2)

### 2.1 Add Reconfigure Step to Config Flow

**File:** `config_flow.py`

```python
async def async_step_reconfigure(
    self, user_input: dict[str, Any] | None = None
) -> FlowResult:
    """Handle reconfiguration of the integration."""
    entry = self._get_reconfigure_entry()

    if user_input is not None:
        # Validate new connection
        try:
            await self._test_connection(
                user_input[CONF_HOST],
                user_input[CONF_PORT],
            )
        except CannotConnect:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=self._get_reconfigure_schema(entry),
                errors={"base": "cannot_connect"},
            )

        # Update config entry
        self.hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, **user_input},
            unique_id=f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}",
        )
        await self.hass.config_entries.async_reload(entry.entry_id)
        return self.async_abort(reason="reconfigure_successful")

    return self.async_show_form(
        step_id="reconfigure",
        data_schema=self._get_reconfigure_schema(entry),
    )

def _get_reconfigure_schema(self, entry: ConfigEntry) -> vol.Schema:
    """Return schema for reconfigure step."""
    return vol.Schema({
        vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str,
        vol.Required(CONF_PORT, default=entry.data[CONF_PORT]): int,
    })
```

### 2.2 Add Strings for Reconfigure

**File:** `strings.json` (update)

```json
{
  "config": {
    "step": {
      "reconfigure": {
        "title": "Reconfigure SolarEdge Connection",
        "description": "Update the connection settings for your SolarEdge inverter.",
        "data": {
          "host": "IP Address",
          "port": "Modbus TCP Port"
        }
      }
    },
    "abort": {
      "reconfigure_successful": "Connection settings updated successfully."
    }
  }
}
```

---

## Phase 3: Translation Support (Week 2-3)

### 3.1 Create Translation Files

**Directory structure:**
```
custom_components/solaredge_modbus_multi/
├── translations/
│   ├── en.json      (English - base)
│   ├── de.json      (German)
│   ├── es.json      (Spanish)
│   ├── fr.json      (French)
│   ├── nl.json      (Dutch)
│   ├── it.json      (Italian)
│   └── pt.json      (Portuguese)
```

### 3.2 English Base Translation

**File:** `translations/en.json`

```json
{
  "config": {
    "step": {
      "user": {
        "title": "SolarEdge Modbus Connection",
        "description": "Enter the connection details for your SolarEdge inverter.",
        "data": {
          "name": "Name",
          "host": "IP Address",
          "port": "Modbus TCP Port",
          "device_list": "Inverter Unit IDs (comma-separated)"
        }
      },
      "options": {
        "title": "Configure Options",
        "data": {
          "scan_interval": "Scan Interval (seconds)",
          "detect_meters": "Detect Meters",
          "detect_batteries": "Detect Batteries",
          "detect_extras": "Detect Extra Features",
          "keep_modbus_open": "Keep Connection Open",
          "adv_storage_control": "Enable Storage Control",
          "adv_site_limit_control": "Enable Site Limit Control"
        }
      },
      "reconfigure": {
        "title": "Reconfigure Connection",
        "description": "Update the connection settings.",
        "data": {
          "host": "IP Address",
          "port": "Modbus TCP Port"
        }
      }
    },
    "error": {
      "cannot_connect": "Cannot connect to inverter. Check IP and port.",
      "invalid_device": "No valid SolarEdge inverter found at this address.",
      "timeout": "Connection timed out. Check network connectivity.",
      "already_configured": "This inverter is already configured."
    },
    "abort": {
      "already_configured": "This inverter is already configured.",
      "reconfigure_successful": "Connection updated successfully."
    }
  },
  "options": {
    "step": {
      "init": {
        "title": "SolarEdge Options",
        "data": {
          "scan_interval": "Update Interval (seconds)",
          "detect_meters": "Auto-detect Meters",
          "detect_batteries": "Auto-detect Batteries",
          "detect_extras": "Enable Extra Features",
          "keep_modbus_open": "Persistent Connection",
          "adv_storage_control": "Storage Control (Advanced)",
          "adv_site_limit_control": "Site Limit Control (Advanced)",
          "sleep_after_write": "Delay After Write (seconds)",
          "battery_rating_adjust": "Battery Rating Adjustment (%)"
        }
      }
    }
  },
  "entity": {
    "sensor": {
      "ac_power": {
        "name": "AC Power"
      },
      "ac_power_inverted": {
        "name": "AC Power (Inverted)"
      },
      "dc_power": {
        "name": "DC Power"
      },
      "ac_energy": {
        "name": "Total Energy"
      },
      "ac_current": {
        "name": "AC Current"
      },
      "ac_voltage": {
        "name": "AC Voltage"
      },
      "dc_voltage": {
        "name": "DC Voltage"
      },
      "dc_current": {
        "name": "DC Current"
      },
      "temperature": {
        "name": "Temperature"
      },
      "status": {
        "name": "Status"
      },
      "battery_power": {
        "name": "Battery Power"
      },
      "battery_power_inverted": {
        "name": "Battery Power (Inverted)"
      },
      "battery_soc": {
        "name": "Battery State of Charge"
      },
      "battery_soh": {
        "name": "Battery State of Health"
      }
    }
  },
  "issues": {
    "check_configuration": {
      "title": "Connection Error",
      "description": "Unable to connect to SolarEdge inverter at {host}:{port}. Please check:\n- The inverter is powered on\n- Modbus TCP is enabled on the inverter\n- The IP address and port are correct\n- No firewall is blocking the connection"
    }
  }
}
```

### 3.3 Update Manifest for Translations

Translations are auto-loaded from `translations/` directory - no manifest change needed.

---

## Phase 4: Documentation (Week 3)

### 4.1 User Documentation

**Create/Update Wiki Pages:**

1. **Getting Started**
   - Prerequisites (HA version, network access)
   - How to enable Modbus TCP on SolarEdge inverter
   - Step-by-step setup with screenshots
   - Common setup issues and solutions

2. **Configuration Options**
   - Explain each option in plain language
   - Recommended settings for different scenarios
   - When to use persistent connection

3. **Entities Reference**
   - List all sensors with descriptions
   - Explain what each value means
   - Units and scale factors
   - Which entities to use for Energy Dashboard

4. **Troubleshooting**
   - Connection issues
   - Timeout problems
   - Missing devices
   - Data not updating
   - How to enable debug logging

5. **FAQ**
   - "Why do I need the inverted sensors?"
   - "How often does data update?"
   - "Can I control my inverter?"
   - "Multiple inverters setup"

### 4.2 Entity Descriptions

**Add EntityDescription to sensors:**

```python
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)

INVERTER_SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="ac_power",
        name="AC Power",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
    ),
    SensorEntityDescription(
        key="ac_energy",
        name="Total Energy",
        native_unit_of_measurement="Wh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:lightning-bolt",
    ),
    # ... more descriptions
)
```

---

## Phase 5: Final Polish (Week 4)

### 5.1 Code Quality

- [ ] Run flake8 with project settings
- [ ] Run isort for import ordering
- [ ] Run black for formatting
- [ ] Add missing docstrings
- [ ] Review all TODOs in code

### 5.2 Quality Scale YAML

**Create `quality_scale.yaml`:**

```yaml
rules:
  # Bronze (all met)
  config-flow: done
  test-before-configure: done
  unique-config-entry: done

  # Silver (all met)
  log-when-unavailable: done
  reauthentication-flow:
    status: exempt
    comment: Modbus TCP does not use authentication
  test-before-setup: done

  # Gold (target)
  devices: done
  discovery:
    status: exempt
    comment: Modbus TCP requires manual IP configuration
  docs-configuration-parameters: done
  docs-installation-parameters: done
  entity-category: done
  entity-device-class: done
  entity-translations: done
  integration-owner: done
  reconfiguration-flow: done
  repair-issues: done
  stale-devices: done
  test-coverage: done
```

### 5.3 Pre-Release Checklist

- [ ] All tests passing (80%+ coverage)
- [ ] Translations complete for en, de, es, fr
- [ ] Reconfigure flow working
- [ ] Documentation updated
- [ ] Entity descriptions added
- [ ] No linting errors
- [ ] CHANGELOG updated
- [ ] Version bumped appropriately

---

## Timeline Summary

| Week | Phase | Deliverables |
|------|-------|-------------|
| 1 | Testing | 80%+ test coverage, CI/CD |
| 2 | Reconfigure + Translations | Reconfigure flow, en.json, de.json |
| 3 | Translations + Docs | es.json, fr.json, Wiki updates |
| 4 | Polish | Entity descriptions, quality_scale.yaml, release |

---

## Success Metrics

| Metric | Bronze | Silver | Gold | Current |
|--------|--------|--------|------|---------|
| Test Coverage | >0% | >50% | >80% | ~20% |
| Translations | 0 | 1 | 4+ | 0 |
| Reconfigure Flow | No | No | Yes | No |
| Documentation | Basic | Good | Excellent | Basic |
| Entity Descriptions | No | No | Yes | No |

---

## Commands Reference

```bash
# Run tests with coverage
pytest tests/ --cov=custom_components.solaredge_modbus_multi --cov-report=html

# Check linting
flake8 custom_components/solaredge_modbus_multi

# Format code
black custom_components/solaredge_modbus_multi
isort custom_components/solaredge_modbus_multi

# Validate translations
python -m homeassistant.scripts.translations validate
```
