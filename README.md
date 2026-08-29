# OSHA Safety RAG Eval

A RAG system over **29 CFR 1926** (OSHA construction safety), and a **33-case
golden set** that was frozen before the system was ever graded.

The point of this repo is the second half. Building a RAG pipeline over federal
regulation is a weekend; knowing whether it is safe to put in front of a site
foreman is the work. So the eval here is opinionated in three ways:

- **Retrieval and answer are scored separately and never averaged.** "The chunk
  never arrived" and "the chunk arrived and was ignored" are different bugs with
  different fixes. A blended number tells you something broke and nothing about
  where.
- **The corpus is pinned to a fixed date.** Every number the golden set asserts
  — 6 feet, 0.1 f/cc, 200 pounds — was read out of one specific snapshot of the
  regulation. See below.
- **Failing cases are left failing.** Nine of the 33 cases fail retrieval today. None was
  edited to make the suite green; each was instead checked to confirm the
  required chunk is genuinely in the index, so the failure belongs to the
  system, not the case.

---

## The corpus is a frozen snapshot as of **2026-08-20**

This matters enough to be the first thing in the README.

eCFR publishes point-in-time snapshots, not diffs — ask it for "today" and you
get whatever the regulation says today. A golden set that asserts exact
alphanumerics against a corpus that drifts silently is worthless: a failing case
could be your retriever, or it could be a rulemaking that landed last Tuesday,
and you cannot tell which.

So `fetch_osha.py` hardcodes `FETCH_DATE = "2026-08-20"` — Title 29's own
`up_to_date_as_of` value on the day this corpus was first built, and the snapshot
every fact in `golden/golden.jsonl` was verified against.

**The fetch script always reproduces this exact text regardless of when it's
run.** Run it today or in five years and you get byte-identical files. (Verified:
a fresh clone's fetch was diffed against the original corpus — zero differences
across all 29 files, ~420k words.)

```bash
python fetch_osha.py            # the pinned 2026-08-20 snapshot — the default
python fetch_osha.py --latest   # whatever eCFR says TODAY (warns if it has moved)
python fetch_osha.py --date 2025-01-15   # any other point in time
```

`--latest` exists for one deliberate act: checking whether the regulation has
moved out from under the golden set. If it has, diff the changed subparts against
the asserted facts **before** repinning. Changing `FETCH_DATE` is a golden-set
revision, not a version bump.

---

## Quickstart

**On a fresh clone (yours, Anil's, a new machine, anyone's) — nothing derived
exists yet.** No `corpus/`, no `.chroma/` vector index, no `rag_traces.jsonl`.
That's intentional (see "What's in here" below for why), not something broken.
The five commands below rebuild all of it from scratch, deterministically —
same corpus, same chunk count, every time, on any machine, because the fetch
is pinned to a fixed date rather than "whatever eCFR says today."

```bash
git clone https://github.com/PraAIHub/osha-safety-rag-eval.git
cd osha-safety-rag-eval
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env                # add your key — see "API key" below
python fetch_osha.py --superseded   # pulls the pinned snapshot + the
                                     # 1926.95 pre-amendment file 3 golden
                                     # cases (the `superseded` type) depend on
python rag.py ingest                # builds the vector index (~3,000 chunks)
python rag.py ask "At what height does OSHA require fall protection?"
python rag.py eval                  # run against all 33 cases in golden/golden.jsonl
```

**How to know it worked.** `rag.py eval` on a correct setup reproduces
retrieval **24/33** and answer **~15/33** (see `report.md` for the exact
breakdown and why those numbers, not 33/33, are expected — several failures
are deliberate, frozen findings, not bugs to chase). If your numbers are
substantially different, the first two things to check are whether
`--superseded` was actually used, and whether `.env` has the right key (a
shell-exported key can silently shadow it — see `HANDOFF.md`'s "Diagnostic
resolved" section for exactly this failure mode and how it was caught).

**`--superseded` is not optional if you want the real 33-case eval to run
correctly.** Without it, `fetch_osha.py` still succeeds and `rag.py ingest`
still runs — nothing errors — but gold-019/020/021 will fail retrieval for a
reason that has nothing to do with the RAG system (the file they need to find
simply isn't there). If you ever see those three specifically failing and
nothing else looks wrong, this is the first thing to check.

**API key.** `fetch_osha.py` and `rag.py ingest` need no key at all — the corpus
comes from a public API and the embeddings are computed locally by a small
open-source model. Only `rag.py ask` and the LLM-judged half of `rag.py eval`
call a hosted model. Any OpenAI-compatible endpoint works; see `.env.example`.

**First run is slow.** `pip install` pulls torch (~2 GB) via
sentence-transformers, and the first `ingest` downloads the 90 MB embedding model
once. After that, `ingest` is about a minute on CPU.

---

## What's in here

| Path | What it is |
|---|---|
| `fetch_osha.py` | Pulls 29 CFR 1926 from eCFR's public versioner API at the pinned date. **Stdlib only** — no dependencies, so the corpus can be rebuilt before anything is installed. |
| `rag.py` | The system under test. Five traced stages: load → chunk → embed → store → retrieve+answer, plus `eval` and `inspect`. |
| `llm_client.py` | Minimal OpenAI-compatible client: `client()`, a metered and traced `chat()`, and a token `meter`. |
| `trace.py` | Stage-level observability. `stage()` / `event()` / `set_quiet()`, writing JSON Lines to `rag_traces.jsonl`. |
| `golden/golden.jsonl` | **The golden set — 33 cases, the single source of truth.** Machine-readable, frozen. |
| `golden/SCHEMA.md` | The case schema and the scoring discipline: two scorecards, four accuracy sub-scores, and the rules about what may not be edited. |
| `golden/cases.md` | Human-readable write-ups of the cases and why each exists. |
| `report.md` | The RAG Readiness Report — SAFETY pass/fail + ACCURACY sub-scores. |
| `HANDOFF.md` | Project state, decisions taken and deliberately deferred, open questions. |

**Not checked in, because all of it is regenerable:** `corpus/` (rebuild with
`fetch_osha.py --superseded` — byte-identical, that's the whole point of
pinning), `.chroma/` (rebuild with `rag.py ingest`), and `rag_traces.jsonl`.
This is why the Quickstart above exists — running it is the only way anyone,
including you on a different machine, gets from a bare clone to a working index.

## The golden set

33 cases across six types. Nine are safety-class and gate at 100%; twenty-four
are accuracy-class and roll up into four named sub-scores.

| Type | Cases | What it catches |
|---|---|---|
| `exact_string` | 11 | Dense embeddings blur "5 feet" against "6 feet". Heights, depths, load ratings, exposure limits. |
| `multi_hop` | 5 | The answer lives in a section the question never names — §1926.502 cited from inside §1926.501. |
| `superseded` | 3 | Same section number, two dated versions, two different legally-correct answers. Similarity cannot tell them apart; only metadata can. |
| `unanswerable` | 5 | The corpus genuinely does not cover it. The right answer is to say so. |
| `adversarial` | 7 | Prompt injection, persona override, prompt extraction, fake authority, fake system messages, embedded injection in pasted content. |
| `abuse` | 2 | Requests to bless an unsafe shortcut or falsify a record. |

`confidential_leak` and `pii_leak` carry **zero** cases. Public federal
regulatory text has no natural restricted-document or PII surface, and the
decision to synthesize one or declare it out of scope is deliberately open — see
`golden/SCHEMA.md`. `report.md` prints the empty rows rather than omitting them,
because a scorecard with invisible missing rows reads as a clean sweep.

## Reading a run

Every stage appends one JSON object to `rag_traces.jsonl`, and every LLM call
inside those stages lands in the same file as an `llm_call` event under the same
run id. One run, one timeline.

```bash
python rag.py inspect                            # the built-in summary
jq 'select(.kind=="llm_call")' rag_traces.jsonl  # every model call, with tokens
jq 'select(.stage=="chunk")'   rag_traces.jsonl  # what the chunker actually did
```

## Known limitations, on purpose

- **Pure dense retrieval.** No BM25, no reranker, no version filter. The low
  `exact_string` and `superseded` sub-scores this produces are the finding the
  report is built on, not a bug to quietly fix before measuring.
- **The two-scorecard rollup in `rag.py eval` is not implemented.** `cmd_eval`
  prints per-type pass/fail; turning that into the SAFETY/ACCURACY scorecards
  `golden/SCHEMA.md` specifies is the open work. See `HANDOFF.md`.
- **`k=4`, single retrieval pass, no query rewriting.** Baseline by design.

---

## Credits

Built by **Prasanna Rajupeta** and **Anil Narayan**.

Developed as a project on the **Modern AI Pro** Level 2 (AI Practitioner) Evals
track. No course material is reproduced here — the corpus is public federal
regulation from eCFR, and the code, schema, golden set, and report are the
authors' own work.

Corpus: 29 CFR Part 1926, retrieved from the eCFR public versioner API
(`ecfr.gov`). U.S. federal regulations are in the public domain.

## License

MIT — see [LICENSE](LICENSE).
