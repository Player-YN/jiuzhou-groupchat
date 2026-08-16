"""P1 self-driven eval harness tests.

Three tests covering the P1 spec:
  1. ``test_demo_mode_runs`` — invokes the script as a subprocess
     (``python -m scripts.eval_self_driven --demo``) from ``backend/`` and
     asserts exit code 0 + CSV has exactly 5 data rows.
  2. ``test_csv_columns_correct`` — invokes ``--demo`` and parses the
     CSV, asserts the header is exactly the spec-mandated 6 columns.
  3. ``test_score_function_returns_four_components`` — imports the
     score function, calls it with a fake message, asserts each
     breakdown dict has exactly 4 keys (``u``, ``r``, ``c``, ``t``).

Test style: follows the existing ``test_*.py`` convention in this
package — ``sys.path.insert(0, str(_BACKEND_ROOT))`` so ``from
scripts...`` resolves; ``os.environ.setdefault("USE_MOCK_LLM",
"true")`` as a defensive guard even though the script never calls an
LLM.

Run:
    cd backend
    python -m pytest tests/test_eval_harness.py -v
"""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
from pathlib import Path

# Make `from scripts...` and `from app...` work whether pytest is
# invoked from `backend/` or from the repo root.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_ROOT))

# Defensive: the script never calls an LLM, but in case future
# refactors introduce a transitive import, force the mock provider.
os.environ.setdefault("USE_MOCK_LLM", "true")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Spec-mandated CSV column order. Keep in sync with
# `scripts.eval_self_driven.CSV_COLUMNS`.
EXPECTED_CSV_HEADER: tuple[str, ...] = (
    "timestamp",
    "speaker_key",
    "text_preview",
    "top_npc",
    "top_score",
    "breakdown_json",
)


# ---------------------------------------------------------------------------
# Helper: invoke the script as a subprocess
# ---------------------------------------------------------------------------


def _run_demo_cli() -> subprocess.CompletedProcess:
    """Run `python -m scripts.eval_self_driven --demo` and return the result.

    Invokes the script from ``_BACKEND_ROOT`` (so ``-m scripts...`` can
    find the package). Uses ``text=True`` with UTF-8 decoding so the
    CJK `text_preview` round-trips correctly on Windows where the
    default stdout encoding is GBK.
    """
    env = os.environ.copy()
    # Force UTF-8 mode in the child so stdout reconfigure works
    # reliably even on Windows cp936 default locales.
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "scripts.eval_self_driven", "--demo"],
        cwd=str(_BACKEND_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=60,  # demo mode is sub-second; 60s is plenty
    )


# ---------------------------------------------------------------------------
# Test 1: demo mode runs end-to-end and emits 5 data rows
# ---------------------------------------------------------------------------


def test_demo_mode_runs():
    """`--demo` exits 0 and produces a CSV with exactly 5 data rows.

    Spec: "invokes the script via subprocess.run(...) and asserts
    exit code 0 + CSV has 5 data rows."
    """
    result = _run_demo_cli()
    assert result.returncode == 0, (
        f"script exited with {result.returncode}; stderr:\n{result.stderr}"
    )
    # Strip only the trailing newline; preserve CSV-internal line breaks.
    out = result.stdout.rstrip("\n")
    lines = out.split("\n") if out else []
    # 1 header + 5 data rows
    assert len(lines) == 6, (
        f"expected 6 lines (1 header + 5 data), got {len(lines)}:\n"
        + "\n".join(lines)
    )
    # Sanity: every data row should have exactly 6 fields (the header
    # also has 6 fields, so we count both).
    for i, line in enumerate(lines):
        fields = next(csv.reader(io.StringIO(line)))
        assert len(fields) == 6, f"line {i} has {len(fields)} fields: {fields!r}"


# ---------------------------------------------------------------------------
# Test 2: CSV header is exactly the spec-mandated columns
# ---------------------------------------------------------------------------


def test_csv_columns_correct():
    """The CSV header is exactly the 6 spec-mandated columns, in order."""
    result = _run_demo_cli()
    assert result.returncode == 0, (
        f"script exited with {result.returncode}; stderr:\n{result.stderr}"
    )
    # Parse the first line as a CSV row (csv.reader handles quoting).
    first_line = result.stdout.splitlines()[0]
    header = tuple(next(csv.reader(io.StringIO(first_line))))
    assert header == EXPECTED_CSV_HEADER, (
        f"CSV header mismatch.\n"
        f"  expected: {EXPECTED_CSV_HEADER}\n"
        f"  got:      {header}"
    )


# ---------------------------------------------------------------------------
# Test 3: score function returns breakdown with exactly 4 keys
# ---------------------------------------------------------------------------


def test_score_function_returns_four_components():
    """Each breakdown dict has exactly 4 keys: 'u', 'r', 'c', 't'.

    Spec: "imports the score function, calls it with a fake message,
    asserts each breakdown has exactly 4 keys (``"u","r","c","t"``)."
    """
    from scripts.eval_self_driven import ROLE_KEYS, score_message

    # A minimal fake message — only `text` is consumed by the stub.
    msg = {
        "text": "@药师 妈耶, 这波血亏, @白前辈 你意下如何?",
        "speaker_key": "shu-hang",
        "ts": 1_715_000_000,
        "role": "agent",
    }
    scored = score_message(msg, role_keys=ROLE_KEYS)

    # 6 NPCs × 1 (role_key, score, breakdown) tuple each.
    assert len(scored) == len(ROLE_KEYS) == 6, (
        f"expected 6 tuples (one per NPC), got {len(scored)}"
    )

    expected_keys = {"u", "r", "c", "t"}
    seen_keys: set[frozenset[str]] = set()
    for rk, score, breakdown in scored:
        # The tuple shape must be (str, float, dict).
        assert isinstance(rk, str), f"role_key should be str, got {type(rk).__name__}"
        assert isinstance(score, (int, float)), (
            f"score should be numeric, got {type(score).__name__}"
        )
        assert isinstance(breakdown, dict), (
            f"breakdown should be dict, got {type(breakdown).__name__}"
        )
        # The exact 4 keys the spec mandates.
        assert set(breakdown.keys()) == expected_keys, (
            f"breakdown keys mismatch for {rk!r}: "
            f"expected {expected_keys}, got {set(breakdown.keys())}"
        )
        # All values should be numeric (so JSON-encodable as numbers).
        for k, v in breakdown.items():
            assert isinstance(v, (int, float)), (
                f"breakdown[{k!r}] for {rk!r} should be numeric, got {type(v).__name__}"
            )
        seen_keys.add(frozenset(breakdown.keys()))

    # And every NPC's breakdown uses the same 4-key shape (defensive
    # against a future refactor that accidentally specializes one NPC).
    assert seen_keys == {frozenset(expected_keys)}, (
        f"breakdowns across NPCs use inconsistent keys: {seen_keys}"
    )


# ---------------------------------------------------------------------------
# Bonus assertion: breakdown_json is a valid JSON dict with the 4 keys
# ---------------------------------------------------------------------------


def test_breakdown_json_round_trip():
    """The `breakdown_json` CSV column is a valid JSON object with 4 keys.

    Not in the explicit spec, but a useful guardrail: downstream
    consumers parse this column with ``json.loads`` and would silently
    break if we ever emit something else.
    """
    result = _run_demo_cli()
    assert result.returncode == 0, (
        f"script exited with {result.returncode}; stderr:\n{result.stderr}"
    )
    lines = result.stdout.splitlines()
    # Skip the header; iterate the 5 data rows.
    for line in lines[1:]:
        row = next(csv.reader(io.StringIO(line)))
        # 6th column (index 5) is breakdown_json.
        raw = row[5]
        parsed = json.loads(raw)
        assert isinstance(parsed, dict), (
            f"breakdown_json should decode to a dict, got {type(parsed).__name__}"
        )
        assert set(parsed.keys()) == {"u", "r", "c", "t"}, (
            f"breakdown_json keys mismatch: {set(parsed.keys())}"
        )
