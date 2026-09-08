"""The LLM half of this repo: one client, one metered chat() call, one meter.

This replaces the shared course kit (`labs/_kit.py`) that `rag.py` used to
import, so this repository stands alone. Three things are exported and that is
the whole surface:

    client()   an OpenAI-compatible client built from .env
    chat()     one call — metered, traced, retries a burst-limit 429 once
    meter      running token/call tally for the process (the cost habit)

Deliberately NOT carried over from the course kit: the interactive tutor loop,
streaming, and the local zero-shot request classifier (which pulled in
transformers + torch, ~2GB, to tag each call). None of them are used by the RAG
pipeline, and the classifier in particular is a teaching feature, not an eval
feature.

## Where the trace goes

`_kit.chat()` wrote its own `traces.jsonl` alongside the pipeline's
`rag_traces.jsonl`, which meant reconstructing a single query meant joining two
files on a timestamp. Here every LLM call is written through `trace.event()` as
kind `llm_call`, so one run produces ONE timeline: pipeline stages and the model
calls nested inside them, same file, same run id.

    jq 'select(.kind=="llm_call")' rag_traces.jsonl

## Configuration (.env — see .env.example)

    OPENAI_API_KEY    required. MAI_API_KEY is accepted as an alias.
    OPENAI_BASE_URL   any OpenAI-compatible endpoint. Defaults to the Modern AI
                      Pro class proxy, which is what this project was built and
                      measured against.
    OPENAI_MODEL      defaults to "mai" — the class proxy picks the model and
                      ignores this value. Point OPENAI_BASE_URL at OpenAI (or
                      Ollama, or anything else that speaks the same API) and set
                      a real model id here instead.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field

from dotenv import load_dotenv
from openai import OpenAI

from trace import event, say

DEFAULT_BASE_URL = "https://learn.modernaipro.com/api/llm/v1"
DEFAULT_MODEL = "mai"      # the class proxy picks the model; the value is ignored

_PREVIEW = 300             # chars of each message kept in the trace record


# ── env + client ─────────────────────────────────────────────────────────────

def model_name() -> str:
    return (os.environ.get("OPENAI_MODEL", "") or DEFAULT_MODEL).strip()


def load_env() -> None:
    """Load .env into os.environ. Safe to call more than once.

    `.env` wins over an inherited shell variable, deliberately. python-dotenv
    defaults the other way, and that default costs an hour the first time it
    bites: an unrelated `OPENAI_API_KEY` exported in your shell silently
    shadows this project's key, the request goes out with the wrong
    credential, and the endpoint answers with a message about ITS auth — which
    sends you debugging the key you can see in .env instead of the one that was
    actually sent. This repo's documented configuration surface is .env, so
    .env is what takes effect. A shadowed value is announced, not swallowed,
    so a deliberate shell override is still visible.

    Split out of client() so anything needing the RESOLVED configuration —
    rag.fingerprint(), which must work with no key at all — reads the same
    values the API calls will actually use. Reading os.environ before this has
    run reports the fallback defaults instead of what .env says, which for a
    fingerprint means quietly attributing a run to the wrong model and the
    wrong endpoint."""
    before = {k: os.environ.get(k) for k in
              ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")}
    load_dotenv(override=True)
    for name, was in before.items():
        now = os.environ.get(name)
        if was and now and was != now:
            say(f"[dim](.env overrode {name} from your shell environment)[/dim]")


def client() -> OpenAI:
    """Load .env and return a client. Fails with the fix, not a stack trace."""
    load_env()
    key = (os.environ.get("OPENAI_API_KEY", "")
           or os.environ.get("MAI_API_KEY", "")).strip()
    if not key or key.startswith("paste-your"):
        say(
            "\n[red]✗ No API key found.[/red] Copy [bold].env.example[/bold] to "
            "[bold].env[/bold] and set OPENAI_API_KEY.\n"
            "  Any OpenAI-compatible endpoint works — set OPENAI_BASE_URL and "
            "OPENAI_MODEL to match it.\n"
            "  [dim](ingest and retrieval need no key at all; only `ask` and the "
            "judged half of `eval` call a model.)[/dim]\n"
        )
        sys.exit(1)
    os.environ["OPENAI_API_KEY"] = key          # the SDK reads this one
    if not os.environ.get("OPENAI_BASE_URL", "").strip():
        os.environ["OPENAI_BASE_URL"] = DEFAULT_BASE_URL
        say("[dim](no OPENAI_BASE_URL in .env — defaulting to the class proxy)[/dim]")
    return OpenAI()


# ── the session meter ────────────────────────────────────────────────────────

@dataclass
class Meter:
    """Running cost awareness. chat() feeds it; print it any time with show().

    Token counts come from the provider's own `usage` block rather than a local
    tokenizer: it is the number you are actually billed for, and it is the only
    one that reveals a reasoning model burning its whole completion budget on
    hidden thinking — which is exactly the failure `rag.py`'s answer() watches
    `completion_tokens` to catch.
    """
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    by_label: dict = field(default_factory=dict)

    def add(self, usage, label: str = "call") -> None:
        if not usage:
            return
        self.calls += 1
        self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
        self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
        row = self.by_label.setdefault(label, [0, 0])
        row[0] += 1
        row[1] += getattr(usage, "total_tokens", 0) or 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def show(self) -> None:
        parts = " · ".join(f"{k} ×{v[0]} ({v[1]} tok)" for k, v in self.by_label.items())
        say(f"[dim]meter: {self.calls} calls · {self.total_tokens} tokens — {parts}[/dim]")


meter = Meter()


# ── one traced, metered call ─────────────────────────────────────────────────

def _clip(text: str) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= _PREVIEW else text[:_PREVIEW] + "…"


def _trace_call(label: str, messages: list[dict], kw: dict, started: float,
                *, output: str = "", usage=None, error: Exception | None = None) -> None:
    """One span per LLM call, written into the pipeline's trace file."""
    event(
        "llm_call",
        label=label,
        model=model_name(),
        ms=round((time.perf_counter() - started) * 1000),
        ok=error is None,
        error=f"{type(error).__name__}: {error}" if error else None,
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        total_tokens=getattr(usage, "total_tokens", None),
        params={k: v for k, v in kw.items() if k != "messages"},
        messages=[{"role": m.get("role"), "content": _clip(m.get("content", ""))}
                  for m in messages],
        output=_clip(output),
    )


def chat(cli: OpenAI, messages: list[dict], label: str = "chat", **kw) -> str:
    """One metered, traced chat call. kw passes through (max_tokens,
    response_format, …). Waits out a burst-limit 429 once — eval suites are
    bursty by nature, and failing 33 cases because the 9th arrived too fast is
    an artifact of the harness, not a finding about the system."""
    started = time.perf_counter()
    for attempt in (1, 2):
        try:
            resp = cli.chat.completions.create(
                model=model_name(), messages=messages, **kw)
            break
        except Exception as e:  # noqa: BLE001
            if attempt == 1 and "429" in str(e):
                say("[dim](burst limit — waiting 25s)[/dim]")
                time.sleep(25)
                continue
            _trace_call(label, messages, kw, started, error=e)
            raise
    meter.add(resp.usage, label)
    out = (resp.choices[0].message.content or "").strip()
    _trace_call(label, messages, kw, started, output=out, usage=resp.usage)
    return out
