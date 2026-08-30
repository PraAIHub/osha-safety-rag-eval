# The corpus — 29 CFR 1926, construction safety

Provenance notes for the text `fetch_osha.py` writes into `corpus/osha-1926/md/`.
That directory is **gitignored** — it is fully regenerable, and pinned, so
checking it in would only add 2.7 MB of duplicated federal register text to every
clone. This file is the part worth keeping in version control.

Everything below was hit live against the real eCFR API while building the fetch
script — not assumed from documentation.

```
python fetch_osha.py --superseded
```

## What's here

- **28 subpart files** (`A-general.md` … `CC-cranes-and-derricks-in-construction.md`)
  — one per eCFR subpart, each section/appendix inside it as a `## ` heading.
  28 files sits inside the project brief's "30–40 documents" range without any
  extra splitting; one subpart in the live pull had zero sections and was
  skipped.
- **1 superseded-version file** (`1926-95-superseded-pre-2024-11-01.md`) — see
  below. Only fetched with `--superseded`.
- **`SNAPSHOT.json`** — written by every run, recording the date actually
  fetched **and a SHA-256 over the 29 `.md` files**. `rag.py ingest` and `rag.py eval` compare it against
  `_meta.corpus_snapshot` on line 1 of `golden/golden.jsonl` and refuse to run
  when they disagree, so the corpus and the golden set cannot silently come
  apart. Gitignored with the rest of the corpus — it describes the local build.

304 sections + 40 appendices, ~420k words total, frozen at the **2026-08-20**
snapshot — Title 29's `up_to_date_as_of` on the day this corpus was first built
(2026-08-24). That date is hardcoded as `FETCH_DATE` in `fetch_osha.py` and is
not derived from the clock, so the fetch reproduces this exact text on any
machine on any day. The default run additionally reports how far live eCFR has
moved past that pin (one `titles.json` call, advisory, `--offline` skips it) —
the pin is reproducible, but it should never be invisible.

## The API, for reference

| Endpoint | Returns |
|---|---|
| `GET /titles.json` | per-title `up_to_date_as_of` — the date to actually request |
| `GET /full/{date}/title-29.xml?part=1926` | the whole part as XML, ~3.5 MB |
| `GET /full/{date}/title-29.xml?part=1926&section=1926.95` | one section, XML root *is* the `<DIV8>` (no wrapper — different shape than the whole-part fetch, see `fetch_osha.py`'s `write_superseded`) |
| `GET /versions/title-29.json?part=1926` | amendment history — 133 of 1926's ~306 sections have 2+ recorded dates |

XML shape: `<DIV5 TYPE="PART">` → `<DIV6 TYPE="SUBPART">` → `<DIV8 TYPE="SECTION">`
/ `<DIV9 TYPE="APPENDIX">`, with two subparts (K — Electrical, Y — Diving)
nesting their sections one level deeper inside `<DIV7 TYPE="SUBJGRP">` (subject
groups). `fetch_osha.py`'s `iter_sections()` descends into those; a plain
`findall("DIV8")` on the subpart would silently miss both subparts' sections
entirely — worth knowing before writing your own parser against this API.

## The superseded-version trap, verified

`golden/SCHEMA.md`'s fourth trap is "a superseded version — similarity cannot
tell two versions apart, only metadata can." This corpus's real instance:
**29 CFR 1926.95**, "Criteria for personal protective equipment," paragraph
(c) — OSHA's actual "Personal Protective Equipment in Construction" final
rule (89 FR 100346, Dec 12, 2024).

- **Pre-amendment** (fetched at `2024-11-01`, saved here):
  > (c) *Design.* All personal protective equipment shall be of safe design
  > and construction for the work to be performed.
- **Current**:
  > (c) *Design and selection.* Employers must ensure that all personal
  > protective equipment: (1) Is of safe design and construction for the
  > work to be performed; and (2) Is selected to ensure that it properly
  > fits each affected employee.

Same section number, two different legally-correct answers to "does PPE have
to fit properly?" depending on which date's text you're looking at — a real,
dated, exact-text supersession, not a synthesized one. The current text lives
inside `E-personal-protective-and-life-saving-equipment.md`; the pre-amendment
text is its own standalone file so a weak retriever has a real chance of
citing the wrong one.

Any golden case built on this trap should assert
`must_not_cite: [{"doc": "1926-95-superseded-pre-2024-11-01"}]` when asking a
present-tense question ("does PPE have to fit properly today"), and the
opposite if you ever write a case that deliberately asks about the old rule.

### Why this section, and why only this one

1926.95 is **one candidate among dozens**, not the only supersession in Part
1926. Measured live against `GET /versions/title-29.json?part=1926`: 682 version
records across 347 identifiers, of which **172 have 2+ version records and 51
have 2+ distinct amendment dates**, spread over 27 distinct amendment dates for
the part. Any of those 51 could carry a supersession trap.

One was implemented because the golden set needs one *good* pair, not many. A
usable trap needs the amendment to change the answer to a question a person
would actually ask, in prose clean enough to grade. Most of the heavily-amended
sections fail that bar — the top five by amendment count are chemical exposure
standards (1926.55 gases/vapors, 9 dates; 1926.1124 cadmium, 8; 1926.1127, 7;
1926.62 lead, 6; 1926.1101 asbestos, 6) whose deltas are numeric table cells and
appendix revisions. "Did a PEL table change in a 2019 appendix revision?" is a
poor golden case: the diff is buried in a table, awkward to phrase naturally,
and awkward to grade.

1926.95(c) is the better fixture on every axis — "does PPE have to fit?" is a
question a site supervisor actually asks, the text is plain prose rather than a
table, the two answers are unambiguous and opposite, and it traces to a single
Federal Register citation. Three golden cases (gold-019/020/021) exercise it. A
second section would add fetch cost and corpus size without testing a new
failure mode: the mechanism under test is that **similarity cannot separate two
versions of the same section number**, and one clean pair demonstrates that as
completely as ten would.

**To add another trap**, pick an identifier from the 51 with a prose-level
change, add it alongside `SUPERSEDED_SECTION`/`SUPERSEDED_DATE` in
`fetch_osha.py`, and write the cases. Nothing in `rag.py` needs to change — the
superseded file is just another `.md` in the same folder, chunked and retrieved
identically to every other document. Confirm the pre-amendment date by fetching
it and reading the text, not by trusting the Federal Register publication date
(see the caution below).

### Caution: the FR publication date is not the cutoff

89 FR 100346 was **published** Dec 12, 2024, and that date appears in the
versions endpoint — but eCFR still serves the **pre-amendment** text of
1926.95(c) on **2024-12-20**, verified by direct fetch. The new "properly fits"
language is not in the corpus on that date. A Federal Register rule is published
first and takes effect later; the versions endpoint records a further amendment
at **2025-01-13**, which is when the text actually changes.

Three distinct dates, easy to conflate:

| Date | What it is |
|---|---|
| **2024-12-12** | 89 FR 100346 published — the "December 2024 amendment" |
| **2025-01-13** | Effective date — when eCFR's served text actually changes |
| **2024-11-01** | The snapshot saved as the superseded file |

This does **not** contradict gold-020's frozen assertion that the fit
requirement "was added by the December 2024 amendment." That is correct: the
December 2024 rulemaking added it, and it took effect in January 2025. The
rulemaking date and the text-change date are different facts.

What it does mean: `SUPERSEDED_DATE = "2024-11-01"` is a safe choice —
comfortably pre-amendment — but it is not "the last pre-amendment date," and
anyone fetching 2024-12-20 expecting the new text will get the old one. When
adding a trap, always confirm the boundary by fetching both sides and diffing
the text rather than trusting the FR date.

## The digest: reproducibility, checked rather than claimed

Every fetch writes `corpus_sha256` into `SNAPSHOT.json` — SHA-256 over each
`.md` file's name and raw bytes, in sorted order. `golden.jsonl`'s `_meta`
records the expected value, and `rag.py` recomputes it live before ingest or
eval. Current: `e53c64e43c86…` over 29 files.

The snapshot *date* catches "wrong day." The digest catches three failures the
date cannot see, all of which let the eval run and print numbers that mean
nothing:

| Failure | Why the date check misses it |
|---|---|
| `--superseded` was skipped | `SNAPSHOT.json` still says 2026-08-20. But gold-019 then passes **vacuously** — with the repealed text absent there is nothing to wrongly cite, so a broken system scores green on the case built to catch exactly that. |
| The fetch died part-way | eCFR 503s under load. 20 of 29 subparts still stamps the right date; failures then read as retrieval problems. |
| A corpus file was hand-edited | Changing "6 feet" to "5 feet" to turn a red case green is undetectable by date. |

This only works because the writers pass `newline=""`. Without it Python's text
mode rewrites `\n` to `os.linesep`, so a Windows fetch of the identical snapshot
digests differently — while ingesting to identical chunks, because `read_text()`
normalises line endings on the way back in. That combination is the dangerous
one: byte-level difference, behaviourally invisible. The README has claimed
byte-identical output since the first commit; `newline=""` is what makes the
claim true on every platform rather than only the one it was written on.

One sharp edge worth knowing, because nothing in the error message implies it:
the digest globs **every** `*.md` in `corpus/osha-1926/md/`. A stray file in that
folder — scratch notes, an editor backup, something you were diffing — changes
the digest even though the corpus itself is fine. So does an editor that adds a
trailing newline on save. That is arguably correct (the corpus should be exactly
what `fetch_osha.py` wrote, nothing more), but it will surprise someone. Keep
working files anywhere else.

Recovery is the same for every cause and never involves inspecting the hash:

```bash
rm -rf corpus/ && python fetch_osha.py --superseded
```

Override with `--allow-snapshot-mismatch` on `ingest`/`eval` when the difference
is intended. Repinning the corpus means updating `_meta.corpus_sha256` alongside
`_meta.corpus_snapshot` — same golden-set revision, one more field.

## Reproducibility

eCFR publishes point-in-time snapshots, not diffs, so "fetch the current text"
means something different every day. That is why `fetch_osha.py` pins
`FETCH_DATE = "2026-08-20"` rather than defaulting to today: the golden cases
assert exact numbers read out of that specific pull, and a corpus that drifts
underneath them turns every failure into a coin flip between "the retriever is
wrong" and "the regulation changed".

Verified on 2026-08-28: a clean fetch at the pinned date diffed against the
original corpus produced **zero differences** across all 29 files.

`--latest` deliberately fetches today's text instead, and warns when it differs
from the pin. If the regulation has moved, diff the changed subparts against the
golden set's asserted facts *before* repinning — that is a golden-set revision,
not a version bump.
