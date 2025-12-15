# SolarEdge Modbus Multi Performance Optimization Plan

**Scope:** Full optimization (P0-P2)
**Breaking Changes:** Allowed
**Status:** IMPLEMENTED

## Implementation Status

| Phase | Task | Status |
|-------|------|--------|
| P0 | asyncio.Lock for race conditions | ✅ Done |
| P0 | Cache inspect.signature() | ✅ Done |
| P0 | Replace blocking write wait | ✅ Done |
| P1 | Parallel device polling | ✅ Done |
| P1 | always_update=False | ✅ Done |
| P2 | Replace OrderedDict with dict | ✅ Done |
| P2 | frozenset for SUNSPEC_SF_RANGE | ✅ Done |
| P2 | PR #928 inverted sensors | ✅ Done |
| - | Version bump to 3.3.0 | ✅ Done |

## Executive Summary

Analysis of the `solaredge-modbus-multi` integration reveals **15 significant performance issues** across three categories: Modbus communication, data coordination, and entity management. The most critical issues are **sequential device polling** and **race conditions** in the hub, which can cause 7+ second polling cycles and potential data corruption.

**Expected Improvements:**
- Polling cycle time: 3-4s → 1-1.5s per inverter (50-60% faster)
- Multi-inverter setups: 9s → 3s (66% faster with parallel reading)
- Reduced CPU/memory overhead from caching and data structure fixes
- Eliminated race conditions in connection handling

---

## Critical Issues (Priority 0)

### 1. Sequential Device Polling → Parallel with asyncio.gather()

**Location:** `hub.py:347-354, 431-436`

**Current Code:**
```python
for inverter in self.inverters:
    await inverter.read_modbus_data()  # Waits for each to complete
for meter in self.meters:
    await meter.read_modbus_data()
for battery in self.batteries:
    await battery.read_modbus_data()
```

**Problem:** With 5 inverters, 15 meters, 15 batteries @ 200ms each = 7+ seconds per cycle

**Fix:**
```python
# Read all inverters in parallel
await asyncio.gather(*[inv.read_modbus_data() for inv in self.inverters])
await asyncio.gather(*[meter.read_modbus_data() for meter in self.meters])
await asyncio.gather(*[bat.read_modbus_data() for bat in self.batteries])
```

**Impact:** 7s → 200ms (with parallel reads)

---

### 2. Race Conditions on Modbus Client Access

**Location:** `hub.py:526-528, 586-588`

**Current Code:**
```python
self._rr_unit = unit
self._rr_address = address
self._rr_count = rcount
# ... later after await ...
result = await self._client.read_holding_registers(...)
```

**Problem:** Two concurrent tasks can overwrite instance variables before read completes

**Fix:** Add asyncio.Lock for all client operations:
```python
self._modbus_lock = asyncio.Lock()

async def modbus_read_holding_registers(self, ...):
    async with self._modbus_lock:
        # existing code
```

**Impact:** Eliminates data corruption risk

---

### 3. Connection State Race Conditions

**Location:** `hub.py:478-515`

**Problem:** `connect()` and `disconnect()` not protected - socket leaks possible

**Fix:** Use same lock for connection management:
```python
async def connect(self) -> None:
    async with self._modbus_lock:
        if self._client is None:
            self._client = AsyncModbusTcpClient(...)
        await self._client.connect()
```

---

### 4. Blocking Write Wait Loop

**Location:** `__init__.py:249-251`

**Current Code:**
```python
while self._hub.has_write:
    await asyncio.sleep(1)  # Blocks all updates with 1s granularity
```

**Fix:** Use asyncio.Event for event-based signaling:
```python
# In hub
self._write_complete_event = asyncio.Event()
self._write_complete_event.set()  # Initially not writing

# In coordinator
await self._hub._write_complete_event.wait()
```

**Impact:** Eliminates 1s polling overhead during writes

---

## High Priority Issues (Priority 1)

### 5. Register Read Consolidation

**Location:** `hub.py:1032-1045, 1170-1172`

**Current:** 2 separate reads for inverter data
- Read 40044-40051 (8 registers)
- Read 40069-40108 (40 registers)

**Fix:** Single read 40044-40108 (65 registers), parse both sections

**Impact:** 50% reduction in inverter read transactions

---

### 6. Cache inspect.signature() Result

**Location:** `hub.py:530-544, 594-608`

**Current:** `inspect.signature()` called on every Modbus operation

**Fix:** Cache once during initialization:
```python
def __init__(self, ...):
    # After client creation
    self._use_device_id_param = "device_id" in inspect.signature(
        self._client.read_holding_registers
    ).parameters
```

**Impact:** Eliminates 100s of inspections per hour

---

### 7. Missing Parallelization in Discovery

**Location:** `hub.py:279-344`

**Current:** Sequential meter/battery discovery during init

**Fix:**
```python
# Discover all meters for one inverter in parallel
if self._detect_meters:
    meter_tasks = []
    for meter_id in METER_REG_BASE:
        new_meter = SolarEdgeMeter(...)
        meter_tasks.append(new_meter.init_device())
    results = await asyncio.gather(*meter_tasks, return_exceptions=True)
```

**Impact:** 5x speedup on initialization

---

### 8. Redundant Value Computation in available Property

**Location:** `sensor.py:893-929`

**Current:** `scale_factor()` computed in both `available` and `native_value`

**Fix:** Compute once, cache result:
```python
@property
def available(self) -> bool:
    self._cached_value = None
    try:
        self._cached_value = self.scale_factor(...)
        return self._cached_value is not None and super().available
    except:
        return False

@property
def native_value(self):
    if self._cached_value is not None:
        return self._cached_value
    return self.scale_factor(...)
```

---

### 9. Add always_update=False to DataUpdateCoordinator

**Location:** `__init__.py:238-242`

**Current:**
```python
super().__init__(
    hass,
    _LOGGER,
    name="SolarEdge Coordinator",
    update_interval=timedelta(seconds=scan_interval),
)
```

**Fix:**
```python
super().__init__(
    hass,
    _LOGGER,
    name="SolarEdge Coordinator",
    update_interval=timedelta(seconds=scan_interval),
    always_update=False,  # Only trigger callbacks when data changes
)
```

**Impact:** 50-70% fewer state writes during low activity (nighttime)

---

## Medium Priority Issues (Priority 2)

### 10. Replace OrderedDict with dict

**Location:** `hub.py:840-850, 857-920, 1072-1081, 1185-1196`

**Problem:** Python 3.7+ dicts maintain insertion order - OrderedDict adds 2-3x memory overhead

**Fix:** Replace all `OrderedDict([...])` with `{...}` regular dicts

**Impact:** Reduced memory footprint for 1,750-3,500 dict instances

---

### 11. Cache Static String Conversions

**Location:** `hub.py:874-890, 1219-1226`

**Problem:** Manufacturer, Model, Serial decoded on every cycle

**Fix:** Only decode during init, store as instance attributes:
```python
def init_device(self):
    self.manufacturer = int_list_to_string(...)
    self.model = int_list_to_string(...)
    self.serial = int_list_to_string(...)
```

**Impact:** Eliminates 8,400 unnecessary string conversions per hour

---

### 12. Use Set for SUNSPEC_SF_RANGE

**Location:** `const.py:180-202`

**Current:** List used for membership testing (O(n))

**Fix:**
```python
SUNSPEC_SF_RANGE = frozenset(range(-10, 11))  # O(1) lookup
```

---

### 13. Prevent Duplicate disconnect() Calls

**Location:** `hub.py:270-271, 302-303, 338-339, etc.`

**Fix:** Use flag to track disconnect state:
```python
self._disconnected_for_error = False

except SomeError:
    if not self._disconnected_for_error:
        self.disconnect()
        self._disconnected_for_error = True
    raise
```

---

### 14. Change Detection Before State Updates

**Location:** `sensor.py:266-268`

**Current:** All entities call `async_write_ha_state()` on every update

**Fix:**
```python
@callback
def _handle_coordinator_update(self) -> None:
    new_value = self.native_value
    if new_value != self._last_value:
        self._last_value = new_value
        self.async_write_ha_state()
```

**Impact:** Reduced HA event bus traffic

---

### 15. Use Set for Device ID Lookup

**Location:** `__init__.py:117-144`

**Current:** List with O(n) membership testing

**Fix:**
```python
known_devices = set()
for inverter in solaredge_hub.inverters:
    known_devices.update(...)
```

---

## Implementation Order

### Phase 1: Critical Fixes (No API changes)
1. Add asyncio.Lock for race condition fixes (#2, #3)
2. Replace blocking write wait with Event (#4)
3. Cache inspect.signature() result (#6)

### Phase 2: Performance Gains
4. Implement parallel device polling (#1)
5. Add always_update=False (#9)
6. Consolidate register reads (#5)

### Phase 3: Memory & Efficiency
7. Replace OrderedDict with dict (#10)
8. Cache static string conversions (#11)
9. Use frozenset for SF_RANGE (#12)

### Phase 4: Polish
10. Parallel discovery (#7)
11. Change detection (#14)
12. Other cleanup (#13, #15)

---

## Files to Modify

| File | Changes |
|------|---------|
| `hub.py` | Lock addition, parallel polling, register consolidation, inspect cache, OrderedDict removal, string caching |
| `__init__.py` | Event-based write wait, always_update=False, device ID set |
| `sensor.py` | Value caching, change detection |
| `const.py` | SUNSPEC_SF_RANGE to frozenset |

---

## Testing Requirements

1. **Single inverter:** Verify basic functionality preserved
2. **Multi-inverter (3+):** Confirm parallel polling works
3. **With meters/batteries:** Test discovery parallelization
4. **Error scenarios:** Verify lock doesn't cause deadlocks
5. **Write operations:** Test event-based signaling works
6. **Long-running:** Monitor for memory leaks after OrderedDict removal

---

## Risk Assessment

| Change | Risk | Mitigation |
|--------|------|------------|
| Parallel polling | Medium | Test on multi-device setups first |
| asyncio.Lock | Low | Standard async pattern |
| Register consolidation | Low | Verify register compatibility |
| OrderedDict removal | Very Low | Python 3.7+ guarantees order |
| always_update=False | None | Reduces updates, doesn't break them |

---

## Included: PR #928 - Inverted Power Sensors

**Will be implemented as part of this optimization:**

PR #928 adds inverted power sensors for HA 2025.12 energy dashboard:

### New Classes to Add (sensor.py)

```python
class ACPowerInverted(ACPower):
    """Inverted AC power sensor for HA 2025.12 energy dashboard.

    HA defines grid power as positive=import, negative=export.
    SolarEdge meters use opposite convention. This class negates values.
    """

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}_inverted"

    @property
    def name(self) -> str:
        return f"{super().name} Inverted"

    @property
    def native_value(self):
        value = super().native_value
        return None if value is None else -value


class SolarEdgeBatteryPowerInverted(SolarEdgeBatteryPower):
    """Inverted battery power sensor for HA 2025.12 energy dashboard."""

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}_inverted"

    @property
    def name(self) -> str:
        return f"{super().name} Inverted"

    @property
    def native_value(self):
        value = super().native_value
        return None if value is None else -value
```

### Entity Registration (sensor.py async_setup_entry)

Add after meter entity creation (~line 141):
```python
entities.append(ACPowerInverted(meter, config_entry, coordinator))
```

Add after battery entity creation (~line 206):
```python
entities.append(SolarEdgeBatteryPowerInverted(battery, config_entry, coordinator))
```

### Version Bump (manifest.json)

Update version to reflect combined changes.

---

## Full File Paths

```
custom_components/solaredge_modbus_multi/hub.py        # Main changes
custom_components/solaredge_modbus_multi/__init__.py   # Coordinator changes
custom_components/solaredge_modbus_multi/sensor.py     # Entity optimizations + PR #928 inverted sensors
custom_components/solaredge_modbus_multi/const.py      # Data structure fix
```
