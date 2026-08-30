"""Download 29 CFR 1926 (OSHA construction safety) into corpus/osha-1926/md/.

    python fetch_osha.py               # the pinned 2026-08-20 snapshot (default)
    python fetch_osha.py --superseded  # + a real pre/post amendment pair
    python fetch_osha.py --latest      # whatever eCFR is current TODAY (see below)
    python fetch_osha.py --date 2025-01-15   # any other point in time
    python fetch_osha.py --offline     # pinned, and skip the live drift check

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

A pin nobody checks is how a corpus quietly ages into misstating current federal
safety law, so the default run now also makes one small call to titles.json and
prints how far live eCFR has moved past the pin. It is advisory: it never changes
what gets fetched, it is never fatal, and `--offline` skips it.

Every run stamps corpus/osha-1926/SNAPSHOT.json with the date it actually
fetched. rag.py reads that back and refuses to ingest or eval a corpus whose
snapshot does not match the date golden.jsonl records as verified-against —
the binding between corpus and golden set used to live only in prose.

Source: eCFR's public versioner API — no key, no auth, stdlib only.
    https://www.ecfr.gov/api/versioner/v1/titles.json                     — issue dates
    https://www.ecfr.gov/api/versioner/v1/full/{date}/title-29.xml?part=1926  — full text
    https://www.ecfr.gov/api/versioner/v1/versions/title-29.json?part=1926    — amendment history
All three were hit live while building this script (2026-08-24): Title 29 was
"up to date as of" 2026-08-20, Part 1926 held 29 subparts / ~304 sections + 40
appendices. Re-measured against the versions endpoint on 2026-08-29: 682 version
records over 347 identifiers, of which 172 have 2+ version records and 51 have
2+ DISTINCT amendment dates, across 27 distinct amendment dates for the part.
(An earlier revision of this docstring claimed 133; that figure matches neither
count and was not reproducible — quote the method with the number.)

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

import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
# NB: `date` is deliberately NOT imported from datetime — the snapshot date moves
# through this module as a local named `date`, and importing the class would
# shadow it (or be shadowed by it) in __main__.
from datetime import datetime, timezone
from pathlib import Path

API = "https://www.ecfr.gov/api/versioner/v1"
CORPUS = Path(__file__).resolve().parent / "corpus" / "osha-1926"
DEST = CORPUS / "md"
SNAPSHOT_FILE = CORPUS / "SNAPSHOT.json"
PART = "1926"

# ── the frozen snapshot ──────────────────────────────────────────────────────
# Title 29's own `up_to_date_as_of` on 2026-08-24, the day this corpus was built
# and the day every number in golden/golden.jsonl was verified against. This is
# a default, not a lock: --latest and --date override it. Changing this constant
# changes the corpus underneath a frozen golden set, so treat it as a golden-set
# revision and re-verify the asserted numbers, not as a version bump.
FETCH_DATE = "2026-08-20"

# The exact supersession pair confirmed above. Three dates matter and they are
# NOT the same one:
#   Dec 12, 2024  — 89 FR 100346 published (the "December 2024 amendment")
#   Jan 13, 2025  — the effective date: when eCFR's served text actually changes
#   2024-11-01    — the snapshot saved as the superseded file, below
# A Federal Register rule is published first and takes effect later, so the FR
# date is not the cutoff: eCFR still returns the PRE-amendment text of 1926.95(c)
# on 2024-12-20 (verified by direct fetch). 2024-11-01 is therefore a safe
# pre-amendment choice, but it is NOT "the last pre-amendment date" — the text
# changes at the 2025-01-13 record, the only amendment date after 2024-12-20.
SUPERSEDED_SECTION = "1926.95"
SUPERSEDED_DATE = "2024-11-01"


def get(url: str, timeout: int = 60, tries: int = 4) -> bytes:
    """Fetch with backoff on transient server errors.

    eCFR returns 503 under load, and the whole-part fetch is a ~3.5 MB request,
    so a run that pulls 1926 twice in a minute will meet one. A transient 503 is
    not the same failure as a 404 on the pinned date — that would mean eCFR
    stopped serving the snapshot the golden set is built on — so only the 5xx
    family is retried, and the 4xx is left to surface immediately."""
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == tries:
                raise
            wait = 5 * 2 ** (attempt - 1)          # 5s, 10s, 20s
            print(f"  … eCFR said {e.code}; retrying in {wait}s "
                  f"({attempt}/{tries - 1})")
            time.sleep(wait)
        except urllib.error.URLError as e:
            if attempt == tries:
                raise
            wait = 5 * 2 ** (attempt - 1)
            print(f"  … {e.reason}; retrying in {wait}s ({attempt}/{tries - 1})")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def current_date(tries: int = 4) -> str:
    """The title's own 'up to date as of' date — NOT today's date. The API 404s
    if you ask for a date past what it has actually indexed yet.

    Reached via --latest, and via the drift check on the default path. The fetch
    of the corpus itself still uses the pinned FETCH_DATE and never depends on
    what eCFR published today; --offline skips the drift check entirely."""
    titles = json.loads(get(f"{API}/titles.json", tries=tries))["titles"]
    t29 = next(t for t in titles if t["number"] == 29)
    return t29["up_to_date_as_of"]


def report_drift() -> None:
    """Print how far the live regulation has moved past the pinned snapshot.

    Advisory only — never fatal, never changes what gets fetched. The pin exists
    so a red golden case means the retriever broke, not that Congress moved; but
    a pin nobody ever checks silently ages into a corpus that misstates current
    federal safety law. This is the cheap way to keep that visible: one small
    JSON request, and a line telling you whether a --latest diff is worth doing.

    Retries are suppressed (tries=1) — this is a nicety on the way to the real
    fetch, and it should not add a minute of backoff to a run that is going to
    succeed regardless."""
    try:
        live = current_date(tries=1)
    except Exception as e:  # noqa: BLE001 — offline is a fine reason to skip
        print(f"  drift check skipped ({type(e).__name__}) — fetching the pin anyway\n")
        return
    if live == FETCH_DATE:
        print(f"  ✓ eCFR is still up to date as of {FETCH_DATE} — no drift\n")
        return
    try:
        days = (datetime.fromisoformat(live).date()
                - datetime.fromisoformat(FETCH_DATE).date()).days
        span = f"{days} days" if days >= 0 else f"{-days} days AHEAD of live"
    except ValueError:
        span = "unknown span"
    print(f"  ⚠ drift: pinned {FETCH_DATE} · eCFR now up to date as of {live} "
          f"({span}).\n"
          f"    The corpus you are about to fetch is still the pinned one, which "
          f"is correct.\n"
          f"    To evaluate the move:  python fetch_osha.py --latest   then diff "
          f"the changed\n"
          f"    subparts against golden/golden.jsonl BEFORE repinning — that is a "
          f"golden-set\n"
          f"    revision, not a version bump.\n")


def corpus_digest(folder: Path | None = None) -> str:
    """SHA-256 over every .md in the corpus — the reproducibility claim, checkable.

    Hashes (filename, raw bytes) for each file in sorted order, so the result
    does not depend on filesystem listing order. Raw BYTES, not decoded text:
    the point is to catch a corpus that differs on disk, which is exactly what
    read_text() would paper over by normalising line endings.

    This is only stable across platforms because the writers above pass
    newline="" — without it, Python's text mode rewrites "\n" to os.linesep, and
    a Windows fetch of the identical snapshot would digest differently while
    ingesting to identical chunks. Byte-identity was claimed in the README long
    before anything verified it; this is the verification.

    The digest deliberately covers whatever is in the folder, so a corpus
    fetched WITHOUT --superseded digests differently from one fetched with it.
    That is a feature: skipping the flag silently invalidates the eval (gold-019
    stops testing anything, see README), and this turns that into a hard stop."""
    h = hashlib.sha256()
    for path in sorted((folder or DEST).glob("*.md")):
        h.update(path.name.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def write_snapshot(date: str, how: str, files: int) -> None:
    """Stamp the corpus with the snapshot it was built from.

    The golden set asserts exact numbers read out of ONE snapshot, and until now
    that binding lived only in prose across four files. This is the machine-
    readable half: rag.py reads it back and refuses to ingest or eval a corpus
    whose date does not match what golden.jsonl says it was verified against."""
    SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_FILE.write_text(json.dumps({
        "corpus_snapshot": date,
        "how": how,
        "title": 29,
        "part": PART,
        "files": files,
        "md_files": len(list(DEST.glob("*.md"))),
        "corpus_sha256": corpus_digest(),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pinned_default": FETCH_DATE,
    }, indent=2) + "\n", encoding="utf-8", newline="")
    print(f"  ↓ {SNAPSHOT_FILE.name}  (corpus_snapshot={date}, "
          f"sha256={corpus_digest()[:12]}…)")


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
        path.write_text("\n".join(lines), encoding="utf-8", newline="")
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
        f"## {heading}\n\n{body}\n", encoding="utf-8", newline="")
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
    date = "(not yet resolved)"      # bound before the try so the 4xx/5xx handler
    try:                             # can name it even if resolve_date() is what failed
        date, how = resolve_date(sys.argv)
        print(f"snapshot date: {date}   [{how}]\n")
        if how.startswith("pinned") and "--offline" not in sys.argv:
            report_drift()
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
        # LAST, always: the digest must cover every file this run wrote,
        # including the superseded one. Stamping before that block would hash a
        # corpus the run is not finished building.
        print()
        write_snapshot(date, how, n)
    except urllib.error.HTTPError as e:
        print(f"\n✗ eCFR said {e.code} {e.reason} for {date} after retries.\n"
              f"  5xx is eCFR being busy — wait and re-run; the pinned date does "
              f"not expire.\n"
              f"  404 would mean it stopped serving that snapshot — check "
              f"{API}/titles.json by hand.")
        sys.exit(1)
    print("\nNext:  python rag.py ingest")
