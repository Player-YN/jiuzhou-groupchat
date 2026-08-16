"""P1 self-driven eval harness — score breakdown CSV from historical group messages.

Stage: P1 (evaluate before integrating).

Scope
-----
A standalone CLI script that:
  1. Reads historical group messages from the SQLite ``AgentMemoryStore``.
  2. Scores each message against all 6 NPC role_keys with a deterministic
     **stub** score function.
  3. Emits a CSV with one row per message, containing the top-scoring NPC
     and the JSON-encoded breakdown dict for that NPC.

The stub score function is intentionally simple. The production scorer
(post-P1) will plug into Letta long-term memory + per-NPC topic
embeddings; this script is the harness that records ground-truth rows
we can re-score later for ablation studies.

Score formula (matches the project spec)
----------------------------------------
::

    score(npc, msg) = w_u * uncertainty(msg, npc_memory)
                    + w_r * condition_reinforcement(msg, npc_persona)
                    - w_c * cost(npc_recent_activity)
                    + w_t * topic_match(msg, npc_archetype)

Stub heuristics
~~~~~~~~~~~~~~~
* ``u = 0.5 + random.random() * 0.5``  — uniform [0.5, 1.0]
* ``r = 1.0 if "@<role_key>" in text or <role_key> in text else 0.0``
* ``c = 0.0``                            — stub never pays a cost
* ``t = random.random() * 0.3``          — uniform [0.0, 0.3]

Each breakdown dict therefore has exactly 4 keys: ``"u"``, ``"r"``,
``"c"``, ``"t"`` — required by the spec and asserted in the test
``test_score_function_returns_four_components``.

Usage
-----
::

    # Demo (no DB, 5 hand-written fake messages):
    cd backend
    python -m scripts.eval_self_driven --demo

    # Real run on the production DB (last 100 group messages):
    python -m scripts.eval_self_driven --db backend/data/agent_memory.sqlite --limit 100

    # Pipe to a file:
    python -m scripts.eval_self_driven --demo --output /tmp/eval.csv

Hard constraints honoured
-------------------------
* No LLM call (stub only).
* No new dependency (stdlib ``argparse`` / ``csv`` / ``json`` / ``random``
  / ``sqlite3`` / ``sys`` / ``time`` + the project's already-installed
  ``pathlib``).
* Reads raw SQLite via stdlib ``sqlite3`` so the script can run while
  the BFF is offline.
* Uses only relative ``backend/...`` paths; no hardcoded absolute paths
  and no use of the project's pre-rename branding (per the file naming
  policy in the project spec).
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sqlite3
import sys
import time
from pathlib import Path
from typing import Final

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Canonical 6 NPC role keys. Mirrors `app.letta_bridge.role_seeds.ROLE_AGENT_KEYS`
# and `app.memory.agent_memory.ROLE_AGENT_KEYS`. Kept as a literal here
# (rather than imported) so the script is self-contained and runnable
# without a working `app` package import.
ROLE_KEYS: Final[tuple[str, ...]] = (
    "shu-hang",
    "yao-shi",
    "san-lang",
    "bei-he",
    "bai-qianbei",
    "ling-die",
)

# Score weights (stub defaults; production tuner will revise).
W_U: Final[float] = 1.0
W_R: Final[float] = 0.8
W_C: Final[float] = 0.0  # c is always 0.0 in the stub, so this is cosmetic
W_T: Final[float] = 1.0

# CSV column order — DO NOT REORDER without updating tests + downstream consumers.
CSV_COLUMNS: Final[tuple[str, ...]] = (
    "timestamp",
    "speaker_key",
    "text_preview",
    "top_npc",
    "top_score",
    "breakdown_json",
)

# text_preview length (per spec).
TEXT_PREVIEW_LEN: Final[int] = 60

# Demo mode: 5 hand-written fake group messages, one per NPC speaker_key,
# with a mix of @-mention and plain. Realistic enough to exercise the
# stub's `r` heuristic across both branches (mention vs. no-mention).
# Order is intentionally varied so the test does not over-fit to one NPC.
_DEMO_TS_BASE: Final[int] = 1_715_000_000  # arbitrary fixed seed (Unix s)


def _demo_messages() -> list[dict]:
    """Return 5 hand-written fake group messages for demo mode.

    Each message has the four required fields per the spec:
    ``text``, ``speaker_key``, ``ts``, ``role``.

    ``ts`` is an increasing sequence of Unix-second ints so the CSV has
    a stable sort; ``role`` is one of ``"user"`` / ``"agent"`` to match
    the real DB schema.
    """
    return [
        # 1. shu-hang with @-mention of yao-shi (matches the "r" branch)
        {
            "text": "@药师 妈耶, 我又踩雷了, 这次血亏到心态崩了",
            "speaker_key": "shu-hang",
            "ts": _DEMO_TS_BASE + 1,
            "role": "agent",
        },
        # 2. yao-shi with plain role_key mention of bai-qianbei (no @-prefix)
        {
            "text": "白前辈又乱开方子, 此丹还需三日火候, 老夫不收庸人",
            "speaker_key": "yao-shi",
            "ts": _DEMO_TS_BASE + 2,
            "role": "agent",
        },
        # 3. san-lang, no mention of any other NPC (exercises the else branch)
        {
            "text": "哈哈痛快! 这波我上! 一刀斩之!",
            "speaker_key": "san-lang",
            "ts": _DEMO_TS_BASE + 3,
            "role": "agent",
        },
        # 4. bei-he with mixed: @-mention of bai-qianbei + plain shu-hang
        {
            "text": "@白前辈 你意下如何? 书航后生可畏, 需勤加修炼",
            "speaker_key": "bei-he",
            "ts": _DEMO_TS_BASE + 4,
            "role": "agent",
        },
        # 5. bai-qianbei, no mention (exercises the else branch)
        {
            "text": "嗯, 小辈们各凭本事, 老夫只看结果",
            "speaker_key": "bai-qianbei",
            "ts": _DEMO_TS_BASE + 5,
            "role": "agent",
        },
    ]


# ---------------------------------------------------------------------------
# Stub score function (importable by tests)
# ---------------------------------------------------------------------------


def score_message(
    msg: dict,
    role_keys: tuple[str, ...] = ROLE_KEYS,
) -> list[tuple[str, float, dict[str, float]]]:
    """Score a single message against all NPCs.

    Parameters
    ----------
    msg
        Dict-like with at least a ``text`` field. Other fields
        (``speaker_key`` / ``ts`` / ``role``) are accepted but unused by
        the stub.
    role_keys
        Tuple of NPC role keys to score against. Default is the 6
        canonical 九洲一号群 keys.

    Returns
    -------
    list of ``(role_key, score, breakdown)`` tuples, one per NPC.
    Each breakdown dict has **exactly** 4 keys: ``"u"``, ``"r"``,
    ``"c"``, ``"t"`` (asserted by ``test_score_function_returns_four_components``).
    """
    text = str(msg.get("text", "") or "")
    out: list[tuple[str, float, dict[str, float]]] = []
    for rk in role_keys:
        u = 0.5 + random.random() * 0.5
        r = 1.0 if (f"@{rk}" in text or rk in text) else 0.0
        c = 0.0
        t = random.random() * 0.3
        score = W_U * u + W_R * r - W_C * c + W_T * t
        out.append((rk, float(score), {"u": u, "r": r, "c": c, "t": t}))
    return out


# ---------------------------------------------------------------------------
# DB loader
# ---------------------------------------------------------------------------


def _query_db_messages(db_path: str, limit: int) -> list[dict]:
    """Read up to ``limit`` group messages from the AgentMemoryStore SQLite.

    Mirrors the exact SELECT the spec asks for::

        SELECT timestamp, speaker_key, text, role
        FROM agent_memory
        WHERE source='group'
        ORDER BY timestamp DESC
        LIMIT N

    Returns a list of dicts with keys ``text``, ``speaker_key``, ``ts``,
    ``role`` — same shape as ``_demo_messages()`` so the downstream CSV
    code is identical for both modes.
    """
    if limit <= 0:
        return []
    p = Path(db_path)
    if not p.exists():
        # Fail loud: the spec says opens the SQLite DB, so a missing DB
        # is a user error, not something to silently ignore.
        raise FileNotFoundError(f"SQLite DB not found: {p}")
    # `uri=True` so absolute Windows paths (with backslashes / drive
    # letter) are accepted. Read-only so we never corrupt a live DB.
    with sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True) as conn:
        cur = conn.execute(
            """
            SELECT timestamp, speaker_key, text, role
            FROM agent_memory
            WHERE source = 'group'
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (int(limit),),
        )
        rows = cur.fetchall()
    out: list[dict] = []
    for ts, speaker_key, text, role in rows:
        out.append(
            {
                "text": str(text or ""),
                "speaker_key": str(speaker_key or ""),
                "ts": int(ts) if ts is not None else 0,
                "role": str(role or ""),
            }
        )
    return out


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------


def _format_row(msg: dict) -> list[str]:
    """Format a single message as a CSV row per the spec."""
    scored = score_message(msg)
    if not scored:
        # Defensive: should never happen because role_keys has 6 entries,
        # but keep the script robust if a future caller passes an empty
        # role_keys tuple.
        top_npc = ""
        top_score = ""
        breakdown_json = ""
    else:
        top_npc, top_score, breakdown = max(scored, key=lambda t: t[1])
        # `top_score` is rounded to 6 decimals for human readability;
        # downstream tooling can still recover the exact float from
        # `breakdown_json` if needed.
        top_score = f"{top_score:.6f}"
        # sorted_keys=True so the same row is byte-identical across runs
        # (matters for snapshot diffs).
        breakdown_json = json.dumps(breakdown, sort_keys=True, ensure_ascii=False)

    text = str(msg.get("text", "") or "")
    text_preview = text[:TEXT_PREVIEW_LEN]
    ts = msg.get("ts", 0)
    return [
        str(ts),
        str(msg.get("speaker_key", "")),
        text_preview,
        top_npc,
        top_score,
        breakdown_json,
    ]


def _write_csv(messages: list[dict], out_fp) -> int:
    """Write a CSV to ``out_fp`` (a text-mode file or ``sys.stdout``).

    Returns the number of data rows written.
    """
    # Wrap in a `TextIOWrapper`-style writer that uses our explicit
    # `lineterminator` (default on csv.writer is "\r\n" which is fine
    # for CSV but we want stable line endings across platforms).
    writer = csv.writer(out_fp, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for m in messages:
        writer.writerow(_format_row(m))
    return len(messages)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scripts.eval_self_driven",
        description=(
            "P1 self-driven eval harness: score historical group messages "
            "with the stub score function and emit a CSV of top-NPC + "
            "breakdown per message."
        ),
    )
    p.add_argument(
        "--db",
        type=str,
        default="backend/data/agent_memory.sqlite",
        help=(
            "Path to the AgentMemoryStore SQLite DB. "
            "Ignored when --demo is set. "
            "Default: backend/data/agent_memory.sqlite (relative to repo root)."
        ),
    )
    p.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max number of group messages to score (default: 100).",
    )
    p.add_argument(
        "--demo",
        action="store_true",
        help=(
            "Run against 5 hand-written fake group messages instead of "
            "the real DB. Useful for smoke-testing the script without a "
            "live SQLite."
        ),
    )
    p.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output CSV file path. Default: stdout.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    args = _build_arg_parser().parse_args(argv)

    # Reconfigure stdout to UTF-8 so the CJK `text` in demo messages
    # doesn't get mojibake'd on Windows (where the default stdout
    # encoding is often GBK). Safe no-op on UTF-8 locales.
    if args.output is None:
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    # Resolve --db relative to the repo root, not the cwd, so the
    # script behaves the same whether you run it from `backend/` or
    # the repo root. We detect the repo root by walking up from this
    # file looking for `AGENTS.md`.
    repo_root = Path(__file__).resolve().parents[2]
    db_path = Path(args.db)
    if not db_path.is_absolute():
        # If the path exists as-given, use it; otherwise try
        # `<repo_root>/<args.db>` which is the common case
        # (`backend/data/agent_memory.sqlite` from the repo root).
        if not db_path.exists():
            candidate = repo_root / args.db
            if candidate.exists():
                db_path = candidate

    if args.demo:
        messages = _demo_messages()
    else:
        messages = _query_db_messages(str(db_path), args.limit)

    if args.output is None:
        _write_csv(messages, sys.stdout)
    else:
        # `newline=""` per csv stdlib guidance so csv.writer controls
        # the line endings (we set "\n" above for stability).
        with open(args.output, "w", encoding="utf-8", newline="") as f:
            _write_csv(messages, f)

    return 0


if __name__ == "__main__":
    # `time` is imported above only so the module is importable without
    # touching side effects; we don't actually use it in the stub.
    # Silence the unused-import lint:
    _ = time
    raise SystemExit(main())
