"""Stage-level observability for the RAG pipeline — the whole thing in ~180 lines.

A RAG bug is almost never in the code you're staring at. It's three stages back:
a file that extracted 40 characters, a chunker that cut a fact in half, an
embedder that quietly returned the same vector for everything. You cannot debug
what you did not measure, so every stage here does two things:

    print   a live human summary while it runs
    append  one JSON object per event to rag_traces.jsonl (append-only JSON Lines)

JSON Lines because it is crash-safe (a killed process leaves valid records),
`tail -f`-able, and readable with jq / pandas / DuckDB when this file isn't
enough.

    off       RAG_TRACE=0
    elsewhere RAG_TRACE_FILE=/path/to.jsonl

Three primitives:

    with stage("chunk") as obs:   times the block, writes ONE summary record.
        obs["chunks"] = 214       Anything you put in obs lands in that record.

    event("load", doc="x.md", chars=0)   writes ONE per-item record, right now.

    set_quiet(True)               silences the live half, keeps the trace half.

This module is standalone on purpose: `rich` is the only import that isn't
stdlib. It knows nothing about LLMs, retrieval, or this corpus — llm_client.py
writes its per-call spans through `event()` here, so one run produces one
timeline covering both the pipeline stages and the model calls inside them.
"""

from __future__ import annotations

import json
import os
import statistics
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()

TRACE_FILE = Path(os.environ.get("RAG_TRACE_FILE", "rag_traces.jsonl"))
RUN_ID = uuid.uuid4().hex[:8]      # groups every record from one process run

_quiet = False


def set_quiet(value: bool) -> None:
    """Silence the live half of observability, keep the trace half.

    The eval loop runs 33 queries; 33 full stage dumps would bury the scorecard.
    Tracing is unaffected — quiet changes what YOU see, never what gets
    recorded, because a run you can't reconstruct afterwards is the thing this
    module exists to prevent."""
    global _quiet
    _quiet = value


def say(*args, **kwargs) -> None:
    if not _quiet:
        console.print(*args, **kwargs)


# ── the trace half ───────────────────────────────────────────────────────────

def _write(record: dict) -> None:
    """Append one JSON object. Never raises — observability that can break the
    thing it observes is worse than none."""
    if os.environ.get("RAG_TRACE", "1") == "0":
        return
    try:
        with TRACE_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(
                {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                 "run": RUN_ID, **record},
                ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001 — a full disk must not kill the pipeline
        pass


def event(kind: str, **fields) -> None:
    """One per-item record: a file loaded, a batch embedded, an LLM call made."""
    _write({"type": "event", "kind": kind, **fields})


@contextmanager
def stage(name: str, **fields):
    """Time a pipeline stage and emit one summary record for it.

    Yields a dict — fill it with whatever you learned; it becomes the record.
    An exception still writes the record (with ok=false) before re-raising,
    because the stage that crashed is the one you most need the numbers from.
    """
    obs: dict = {}
    say(f"\n[bold yellow]▸ {name}[/bold yellow]")
    started = time.perf_counter()
    try:
        yield obs
    except Exception as e:  # noqa: BLE001
        _write({"type": "stage", "stage": name, "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "ms": round((time.perf_counter() - started) * 1000), **fields, **obs})
        raise
    ms = round((time.perf_counter() - started) * 1000)
    _write({"type": "stage", "stage": name, "ok": True, "ms": ms, **fields, **obs})
    say(f"  [dim]{name} finished in {ms} ms → traced to {TRACE_FILE}[/dim]")


# ── printing helpers (the live half of observability) ────────────────────────

def kv(label: str, value, note: str = "") -> None:
    say(f"    {label:<22} [bold]{value}[/bold]" + (f"  [dim]{note}[/dim]" if note else ""))


def table(title: str, columns: list[str], rows: list[tuple]) -> None:
    t = Table(title=f"  {title}", title_justify="left", header_style="bold",
              box=None, padding=(0, 2))
    for i, c in enumerate(columns):
        t.add_column(c, justify="left" if i == 0 else "right")
    for r in rows:
        t.add_row(*[str(x) for x in r])
    say(t)


def spread(values: list[float | int], label: str, unit: str = "") -> dict:
    """min / median / p90 / max — the four numbers that expose a bad distribution.

    An average hides the failure: 200 healthy chunks and 3 empty ones average
    out fine. The minimum is what tells you something is broken."""
    if not values:
        kv(label, "—", "no values")
        return {}
    vals = sorted(values)
    p90 = vals[min(int(len(vals) * 0.9), len(vals) - 1)]
    stats = {"min": vals[0], "median": round(statistics.median(vals), 1),
             "p90": p90, "max": vals[-1], "n": len(vals)}
    kv(label, f"{stats['min']}–{stats['max']}{unit}",
       f"median {stats['median']}{unit} · p90 {stats['p90']}{unit} · n={len(vals)}")
    return stats


def histogram(values: list[int | float], bins: int = 6, width: int = 28) -> None:
    """A distribution you can read at a glance beats four summary numbers."""
    if not values:
        return
    lo, hi = min(values), max(values)
    if hi == lo:
        say(f"    [dim]all {len(values)} values = {lo}[/dim]")
        return
    step = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        counts[min(int((v - lo) / step), bins - 1)] += 1
    peak = max(counts) or 1
    for i, c in enumerate(counts):
        bar = "█" * round(width * c / peak)
        say(f"    [dim]{lo + i * step:6.0f}–{lo + (i + 1) * step:<6.0f}[/dim] "
            f"[cyan]{bar}[/cyan] [dim]{c}[/dim]")
