# Repository Guidelines

**TL;DR:** Keep changes scoped to the Home Assistant integration, validate them
with Ruff and pytest, and protect live inverter details and write-capable controls.

## Project Structure & Module Organization

Runtime code lives in `custom_components/solaredge_modbus_multi/`. `__init__.py`
owns integration setup and coordinator lifecycle; `hub.py`, `modbus_transport.py`,
`scanner.py`, and `devices.py` handle Modbus/SunSpec behavior. Platform modules
such as `sensor.py`, `number.py`, and `switch.py` expose Home Assistant entities.
User-facing metadata is in `manifest.json`, `strings.json`, and `translations/`.

Tests live in `tests/` and generally mirror runtime modules. Shared fixtures are
in `conftest.py`, transport tests use `fake_modbus_server.py`, and
`tests/fixtures/*.json` contains golden snapshots. CI is defined in
`.github/workflows/test.yml`.

## Build, Test, and Development Commands

Use Python 3.13 to match CI (the package supports Python 3.12+):

```bash
uv venv --python 3.13
source .venv/bin/activate
uv pip install -r requirements.txt -r requirements_test.txt "ruff==0.8.3" "pre-commit>=3.5.0"
ruff check custom_components/ tests/
ruff format --check custom_components/ tests/
python -m pytest tests/ -q
pre-commit run --all-files
```

Run a focused test while iterating, for example
`python -m pytest tests/test_hub.py -q`. This HACS integration has no separate
build step; manual testing uses a copy under Home Assistant's
`config/custom_components/`.

## Coding Style & Naming Conventions

Use four-space indentation, double quotes, and an 88-character line target. Ruff
enforces E, W, F, I, and B rules plus import sorting. Use `snake_case` for
modules, functions, and fixtures; `PascalCase` for classes; and
`UPPER_SNAKE_CASE` for constants. Reuse existing hub, transport,
entity-description, and fixture patterns.

## Testing Guidelines

Pytest discovers `test_*.py`, `Test*`, and `test_*`; async mode is automatic.
Add focused regression tests for behavior changes, and use the fake server when
protocol or session behavior matters. Change golden fixtures only for deliberate
behavior or entity-metadata updates. No coverage percentage is enforced, but the
full suite must pass before review.

## Commit & Pull Request Guidelines

Recent fork history uses Conventional Commits: `feat:`, `fix:`, `refactor:`,
`test:`, `perf:`, and `chore:`; optional scopes such as `refactor(hub):` are
welcome. Keep commits focused. PRs should summarize behavior and risk, link
issues when relevant, and report exact Ruff/pytest results. Include screenshots
for visible config-flow or UI changes.

## Security & Hardware Safety

Never commit Home Assistant secrets, inverter addresses or serials, diagnostic
exports, or ignored `.claude/` and `DEPLOY_STATUS.md` notes. Keep automated tests
on mocks or the fake server. Treat Modbus writes and advanced power/storage
controls as hardware-affecting, and document safety assumptions.
