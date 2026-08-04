"""Tests for the shared golden assertion itself."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import assert_golden


def _write(tmp_path: Path, payload) -> Path:
    path = tmp_path / "write_golden.json"
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    return path


@pytest.mark.parametrize(
    "drifted",
    [
        [{"payload": [1.0, 0]}],  # float where the wire needs an int
        [{"payload": [True, 0]}],  # bool where the wire needs an int
    ],
    ids=["float", "bool"],
)
def test_golden_rejects_numeric_type_drift(tmp_path, drifted) -> None:
    """1 == 1.0 == True in Python, but not on the wire.

    A parsed-JSON comparison would accept all three, so a float payload could
    pass this gate and only fail against real hardware.
    """
    path = _write(tmp_path, [{"payload": [1, 0]}])
    rendered = json.dumps(drifted, indent=1, sort_keys=True) + "\n"

    with pytest.raises(AssertionError, match="drift"):
        assert_golden(path, rendered, drift="Test drift.")


def test_golden_accepts_an_exact_match(tmp_path) -> None:
    """Regression guard: the comparison is not simply always failing."""
    rows = [{"payload": [1, 0]}]
    path = _write(tmp_path, rows)

    assert_golden(
        path, json.dumps(rows, indent=1, sort_keys=True) + "\n", drift="Test drift."
    )
