"""Download 29 CFR 1926 (OSHA construction safety) into corpus/osha-1926/md/.

    python fetch_osha.py               # the pinned 2026-08-20 snapshot (default)
    python fetch_osha.py --superseded  # + a real pre/post amendment pair
    python fetch_osha.py --latest      # whatever eCFR is current TODAY (see below)
    python fetch_osha.py --date 2025-01-15   # any other point in time

## The pinned date is the point

eCFR publishes point-in-time snapshots, not diffs, so `--latest` pulls whatever
the regulation says on the day you run it. The golden set in golden/golden.jsonl
asserts exact numbers ("6 feet", "0.1 f/cc", "200 pounds") read out of ONE
specific snapshot, so a corpus that drifts silently turns every golden case into
a coin flip: a failure could be the retriever, or it could be Congress.

So FETCH_DATE below is hardcoded to 2026-08-20 — Title 29's own
`up_to_date_as_of` on the day this corpus was first built (2026-08-24), and the
snapshot every number in the golden set was verified against. Run this script
today, or in a year, and you get byte-identical text.

`--latest` exists for the deliberate act of checking whether the regulation has
moved. If it has, diff the changed subparts against the golden set BEFORE
repinning — that is a golden-set revision, not a fetch.

Source: eCFR's public versioner API — no key, no auth, stdlib only.
    https://www.ecfr.gov/api/versioner/v1/titles.json                     — issue dates
    https://www.ecfr.gov/api/versioner/v1/full/{date}/title-29.xml?part=1926  — full text
    https://www.ecfr.gov/api/versioner/v1/versions/title-29.json?part=1926    — amendment history
All three were hit live while building this script (2026-08-24): Title 29 was
"up to date as of" 2026-08-20, Part 1926 held 29 subparts / ~304 sections + 40
appendices, and 133 of those sections have 2+ recorded amendment dates.

Each SUBPART becomes one Markdown file — 29 files, inside the brief's "30-40
documents" range without any extra splitting. Every SECTION and APPENDIX inside
it becomes a `## ` heading, which is exactly the doc-title-plus-nearest-heading
granularity rag.py's chunk() already expects (see its docstring) — so
`must_cite` can target {"doc": "M-fall-protection", "heading": "1926.501"}
with zero changes to the ingest pipeline.

--superseded additionally fetches 29 CFR 1926.95 (Criteria for personal
protective equipment) as it read BEFORE 89 FR 100346 (Dec 12, 2024) — OSHA's
"PPE in Construction" final rule, which added an explicit proper-fit
requirement to (c). Confirmed by diffing the two live pulls while building
this script: old (c) reads "All personal protective equipment shall be of
safe design and construction for the work to be performed"; new (c) is
"Design and selection[:] ... (2) Is selected to ensure that it properly fits
each affected employee." Same section number, two different legal answers to
"does PPE have to fit?", both fetchable from the live API today — a real,
dated, exact-text supersession trap, not a synthesized one.
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

API = "https://www.ecfr.gov/api/versioner/v1"
DEST = Path(__file__).resolve().parent / "corpus" / "osha-1926" / "md"
PART = "1926"

# ── the frozen snapshot ──────────────────────────────────────────────────────
# Title 29's own `up_to_date_as_of` on 2026-08-24, the day this corpus was built
# and the day every number in golden/golden.jsonl was verified against. This is
# a default, not a lock: --latest and --date override it. Changing this constant
# changes the corpus underneath a frozen golden set, so treat it as a golden-set
# revision and re-verify the asserted numbers, not as a version bump.
FETCH_DATE = "2026-08-20"

# The exact supersession pair confirmed above. 2024-11-01 is the last date the
# API still returns 1926.95 pre-amendment; the amendment's effective/FR date is
# Dec 12, 2024.
SUPERSEDED_SECTION = "1926.95"
SUPERSEDED_DATE = "2024-11-01"


def get(url: str, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def current_date() -> str:
    """The title's own 'up to date as of' date — NOT today's date. The API 404s
    if you ask for a date past what it has actually indexed yet.

    Only reached via --latest. The default path uses the pinned FETCH_DATE and
    makes no call here at all, which is also why the default run works offline
    against a warm HTTP cache and never depends on what eCFR published today."""
    import json
    titles = json.loads(get(f"{API}/titles.json"))["titles"]
    t29 = next(t for t in titles if t["number"] == 29)
    return t29["up_to_date_as_of"]


def slug(text: str) -> str:
    m = re.match(r"^Subpart\s+\S+[—–-]\s*(.+)$", text)  # "Subpart M—Fall Protection" -> "Fall Protection"
    text = m.group(1) if m else re.sub(r"^Subpart\s+\S+\s*", "", text)
    text = text.replace("—", " ").replace("–", " ")      # any leftover em/en dash -> space
    text = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[\s_]+", "-", text)[:60]


def clean(elem: ET.Element) -> str:
    return re.sub(r"\s+", " ", "".join(elem.itertext())).strip()


def section_body(div: ET.Element) -> str:
    """Every direct child except HEAD, joined as paragraphs — same '\\n\\n'
    convention chunk() splits on."""
    parts = [clean(child) for child in div if child.tag != "HEAD"]
    return "\n\n".join(p for p in parts if p)


def iter_sections(container: ET.Element):
    """Yield (heading, body) for every SECTION/APPENDIX under `container`, in
    document order, descending into SUBJGRP wrappers (two subparts — K and Y —
    nest all their sections one level deeper under one of these)."""
    for child in container:
        typ = child.attrib.get("TYPE")
        if typ in ("SECTION", "APPENDIX"):
            head = child.find("HEAD")
            heading = clean(head) if head is not None else child.attrib.get("N", "")
            yield heading, section_body(child)
        elif typ == "SUBJGRP":
            yield from iter_sections(child)


def write_subparts(xml_bytes: bytes) -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    root = ET.fromstring(xml_bytes)
    written = 0
    for subpart in root.findall("DIV6"):
        letter = subpart.attrib.get("N", "?")
        head = subpart.find("HEAD")
        title = clean(head) if head is not None else f"Subpart {letter}"
        sections = list(iter_sections(subpart))
        if not sections:
            continue
        lines = [f"# {title}", ""]
        for heading, body in sections:
            lines.append(f"## {heading}")
            lines.append("")
            lines.append(body)
            lines.append("")
        path = DEST / f"{letter}-{slug(title)}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        print(f"  ↓ {path.name}  ({len(sections)} sections/appendices)")
        written += 1
    return written


def write_superseded(date: str, section: str) -> None:
    xml_bytes = get(f"{API}/full/{date}/title-29.xml?part={PART}&section={section}")
    root = ET.fromstring(xml_bytes)
    # &section= makes the SECTION/APPENDIX div itself the document root — no
    # DIV5/DIV6 wrapper to search through, unlike the whole-part fetch above.
    div = root if root.tag in ("DIV8", "DIV9") else (
        root.find(".//DIV8") if root.find(".//DIV8") is not None else root.find(".//DIV9"))
    if div is None:
        print(f"  ✗ {section} not found in the {date} snapshot — skipping")
        return
    head = div.find("HEAD")
    heading = clean(head) if head is not None else section
    body = section_body(div)
    name = f"{section.replace('.', '-')}-superseded-pre-{date}.md"
    path = DEST / name
    path.write_text(
        f"# {section} — SUPERSEDED (text as of {date}, before the amendment)\n\n"
        f"## {heading}\n\n{body}\n", encoding="utf-8")
    print(f"  ↓ {path.name}  (superseded-version trap: compare against the "
          f"current text of {section} in its subpart's file)")


def resolve_date(argv: list[str]) -> tuple[str, str]:
    """(date, how) — the snapshot to fetch and a one-word note on where it came
    from, so the run always prints which of the three it used."""
    if "--date" in argv:
        return argv[argv.index("--date") + 1], "requested"
    if "--latest" in argv:
        return current_date(), "live — eCFR's current up_to_date_as_of"
    return FETCH_DATE, "pinned — the frozen snapshot this golden set asserts"


if __name__ == "__main__":
    print(f"29 CFR Part {PART} → {DEST}\n")
    try:
        date, how = resolve_date(sys.argv)
        print(f"snapshot date: {date}   [{how}]\n")
        if how.startswith("live") and date != FETCH_DATE:
            print(f"  ⚠ {date} is NOT the pinned {FETCH_DATE}. The regulation may "
                  f"have moved under\n    the golden set — diff the changed "
                  f"subparts before trusting a green suite.\n")
        xml_bytes = get(f"{API}/full/{date}/title-29.xml?part={PART}")
        n = write_subparts(xml_bytes)
        print(f"\n{n} subpart files written")
        if "--superseded" in sys.argv:
            print()
            write_superseded(SUPERSEDED_DATE, SUPERSEDED_SECTION)
    except urllib.error.HTTPError as e:
        print(f"\n✗ eCFR said {e.code} {e.reason} for {date}. A 404 on the pinned "
              f"date would mean eCFR\n  stopped serving that snapshot — check "
              f"{API}/titles.json by hand.")
        sys.exit(1)
    print("\nNext:  python rag.py ingest")
