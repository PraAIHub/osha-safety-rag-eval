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

304 sections + 40 appendices, ~420k words total, frozen at the **2026-08-20**
snapshot — Title 29's `up_to_date_as_of` on the day this corpus was first built
(2026-08-24). That date is hardcoded as `FETCH_DATE` in `fetch_osha.py` and is
not derived from the clock, so the fetch reproduces this exact text on any
machine on any day.

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
