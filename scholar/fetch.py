# /// script
# requires-python = ">=3.11"
# dependencies = ["curl_cffi"]
# ///
"""Refresh scholar/data/citations.json, the per-paper citation counts the front page shows.

Usage:  uv run scholar/fetch.py

Google Scholar has no API and refuses browser requests, so the front page cannot ask it
directly. This script loads the public profile, reads every entry's citation count and
writes them to data/citations.json keyed by the entry's Scholar id (the part after the
colon in its citation_for_view parameter). A paper on the front page carries that id in
its data-scholar attribute; ids and titles are listed in the JSON for looking them up.

The file carries an "asof" timestamp that the page shows under the paper list. If Scholar
blocks the request, nothing is written and the previous file stays in place.
"""
import datetime as dt
import html
import json
import re
import sys
from pathlib import Path

from curl_cffi import requests

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
INDEX = HERE.parent / "index.html"
USER = "5rDtuRIAAAAJ"
PAGE = 100


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def fetch_page(start):
    url = "https://scholar.google.com/citations"
    params = {"user": USER, "hl": "en", "cstart": start, "pagesize": PAGE, "sortby": "pubdate"}
    r = requests.get(url, params=params, impersonate="chrome", timeout=30)
    if r.status_code != 200 or "/sorry/" in r.url:
        raise SystemExit(f"scholar refused the request (status {r.status_code}, url {r.url})")
    return r.text


def parse_rows(page):
    papers = {}
    for row in re.findall(r'<tr class="gsc_a_tr">(.*?)</tr>', page, re.S):
        m_id = re.search(r'citation_for_view=' + USER + r':([^"&]+)"[^>]*class="gsc_a_at"[^>]*>(.*?)</a>', row, re.S)
        if not m_id:
            continue
        m_cites = re.search(r'class="gsc_a_ac[^"]*"[^>]*>(\d*)</a>', row, re.S)
        m_cluster = re.search(r'cites=([\d,]+)', row)
        m_year = re.search(r'class="gsc_a_h[^"]*"[^>]*>(\d{4})?</span>', row, re.S)
        entry = {
            "title": html.unescape(re.sub(r"<[^>]+>", "", m_id.group(2))).strip(),
            "year": int(m_year.group(1)) if m_year and m_year.group(1) else None,
            "cites": int(m_cites.group(1)) if m_cites and m_cites.group(1) else 0,
        }
        if m_cluster:
            entry["cluster"] = m_cluster.group(1)
        papers[m_id.group(1)] = entry
    return papers


def main():
    papers, start = {}, 0
    while True:
        page = fetch_page(start)
        rows = parse_rows(page)
        if not rows:
            if start == 0:
                raise SystemExit("no publication rows found; the page layout may have changed")
            break
        papers.update(rows)
        if len(rows) < PAGE or 'id="gsc_bpf_more" disabled' in page:
            break
        start += PAGE

    DATA.mkdir(exist_ok=True)
    (DATA / "citations.json").write_text(
        json.dumps({"asof": now(), "user": USER, "papers": papers}, ensure_ascii=False, indent=1) + "\n"
    )
    print(f"  wrote data/citations.json, {len(papers)} entries")
    for pid, p in sorted(papers.items(), key=lambda kv: -kv[1]["cites"]):
        print(f"  {pid:>14}  {p['cites']:>5}  {p['year'] or '    '}  {p['title'][:80]}")

    status = 0
    if INDEX.exists():
        wanted = re.findall(r'data-scholar="([^"]+)"', INDEX.read_text())
        missing = [w for w in wanted if w not in papers]
        for w in missing:
            print(f"  warning: index.html references scholar id {w}, which is not on the profile", file=sys.stderr)
        if missing:
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
