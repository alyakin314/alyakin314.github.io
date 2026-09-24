# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "curl_cffi"]
# ///
"""Refresh the JSON files under finance/data/ that the dashboard cannot pull in the browser.

Usage:  uv run finance/fetch.py             # everything
        uv run finance/fetch.py stocks cpi  # only some parts

Parts:  btc     Bitstamp daily BTC/USD closes since 2011 (the cycle chart's base series)
        stocks  one year of daily closes for the equity tiles, from Yahoo Finance
        etf     spot Bitcoin ETF daily net flows since launch, from Farside Investors
        cpi     ten years of CPI-U index levels for the release's Table A items, from the BLS API,
                plus the BLS release calendar

Each file carries an "asof" timestamp that the page shows next to the panel.
"""
import datetime as dt
import html
import json
import re
import sys
import time
from pathlib import Path

import requests
from curl_cffi import requests as crequests

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def write(name, obj):
    DATA.mkdir(exist_ok=True)
    (DATA / name).write_text(json.dumps(obj, separators=(",", ":")))
    print(f"  wrote data/{name}")


# ---------------------------------------------------------------- btc

def btc():
    url = "https://www.bitstamp.net/api/v2/ohlc/btcusd/"
    rows, start, end = {}, 1313000000, int(time.time())
    while start < end:
        r = requests.get(url, params={"step": 86400, "limit": 1000, "start": start}, timeout=30).json()
        data = r["data"]["ohlc"]
        if not data:
            break
        for d in data:
            day = dt.datetime.fromtimestamp(int(d["timestamp"]), dt.timezone.utc).date()
            rows[day] = float(d["close"])
        start = int(data[-1]["timestamp"]) + 86400
        time.sleep(0.3)
    days = sorted(rows)
    closes, cur = [], days[0]
    while cur <= days[-1]:
        if cur in rows:
            closes.append(rows[cur])
        else:
            prev = max(d for d in days if d < cur)
            nxt = min(d for d in days if d > cur)
            w = (cur - prev).days / (nxt - prev).days
            closes.append(rows[prev] + w * (rows[nxt] - rows[prev]))
        cur += dt.timedelta(days=1)
    closes = [round(c, 2) if c < 100 else int(round(c)) for c in closes]
    print(f"  {len(closes)} days, {days[0]} .. {days[-1]}, last close {closes[-1]}")
    write("btc.json", {"asof": now(), "start": days[0].isoformat(), "closes": closes})


# ---------------------------------------------------------------- stocks

MEGA = [("NVDA", "Nvidia"), ("MSFT", "Microsoft"), ("META", "Meta"), ("GOOGL", "Alphabet"), ("AMZN", "Amazon"), ("TSM", "TSMC")]
ETFS = [("QQQ", "Nasdaq 100"), ("SPY", "S&P 500"), ("GLD", "Gold")]


def stocks():
    syms = [s for s, _ in MEGA + ETFS]
    url = "https://query1.finance.yahoo.com/v8/finance/spark?symbols=" + ",".join(syms) + "&range=1y&interval=1d"
    for attempt in range(4):  # Yahoo rate-limits bursts; back off and retry
        r = crequests.get(url, impersonate="chrome", timeout=30)
        if r.status_code != 429:
            break
        time.sleep(3 * 2 ** attempt)
    r.raise_for_status()
    j = r.json()
    caps = market_caps(syms)
    out = []
    for sym, name in MEGA + ETFS:
        s = j.get(sym)
        if not s or not s.get("timestamp"):
            print(f"  {sym}: missing from Yahoo response")
            continue
        pts = [(dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat(), c)
               for t, c in zip(s["timestamp"], s["close"]) if c is not None]
        out.append({"symbol": sym, "name": name, "group": "mega" if (sym, name) in MEGA else "etf",
                    "cap": caps.get(sym), "dates": [d for d, _ in pts], "closes": [round(c, 2) for _, c in pts]})
        print(f"  {sym:5} {len(pts)} closes, last {pts[-1][0]} {pts[-1][1]:.2f}, cap {caps.get(sym)}")
    write("stocks.json", {"asof": now(), "symbols": out})


def market_caps(syms):
    """Market cap for equities, net assets for ETFs, in USD. Yahoo's quote endpoint wants a cookie and a crumb."""
    try:
        s = crequests.Session(impersonate="chrome")
        s.get("https://fc.yahoo.com", timeout=30)  # 404, but it sets the cookie the crumb is tied to
        crumb = s.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=30).text
        r = s.get("https://query2.finance.yahoo.com/v7/finance/quote",
                  params={"symbols": ",".join(syms), "crumb": crumb, "fields": "marketCap,netAssets"}, timeout=30)
        r.raise_for_status()
        out = {}
        for q in r.json()["quoteResponse"]["result"]:
            v = q.get("marketCap") or q.get("netAssets")
            if v:
                out[q["symbol"]] = int(v)
        return out
    except Exception as e:  # the tiles simply omit the figure
        print(f"  market caps failed: {e}")
        return {}


# ---------------------------------------------------------------- etf flows

def cells(row):
    return [html.unescape(re.sub(r"<.*?>", "", c)).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.S)]


def num(s):
    s = s.replace(",", "").strip()
    if s in ("", "-"):
        return None
    neg = s.startswith("(")
    v = float(s.strip("()"))
    return -v if neg else v


def etf():
    r = crequests.get("https://farside.co.uk/bitcoin-etf-flow-all-data/", impersonate="chrome", timeout=60)
    r.raise_for_status()
    tables = re.findall(r'<table class="etf".*?</table>', r.text, flags=re.S)
    if not tables:
        raise RuntimeError("no flow table in Farside page")
    rows = re.findall(r"<tr.*?</tr>", tables[0], flags=re.S)
    header = cells(rows[0])
    if header[0] != "Date" or header[-1] != "Total":
        raise RuntimeError(f"unexpected header {header}")
    funds = header[1:-1]
    out = []
    for row in rows[1:]:
        c = cells(row)
        try:
            d = dt.datetime.strptime(c[0], "%d %b %Y").date()
        except ValueError:
            continue
        flows = [num(x) for x in c[1:1 + len(funds)]]
        if all(f is None for f in flows):
            continue
        out.append({"date": d.isoformat(), "flows": flows, "total": num(c[1 + len(funds)])})
    print(f"  {len(funds)} funds, {len(out)} trading days, {out[0]['date']} .. {out[-1]['date']}, last total {out[-1]['total']}")
    write("etf.json", {"asof": now(), "unit": "USD millions", "funds": funds, "rows": out})


# ---------------------------------------------------------------- cpi

# The items of Table A in the BLS CPI news release, with their indent level.
ITEMS = [
    ("SA0", "All items", 0),
    ("SAF1", "Food", 1),
    ("SAF11", "Food at home", 2),
    ("SEFV", "Food away from home", 2),
    ("SA0E", "Energy", 1),
    ("SACE", "Energy commodities", 2),
    ("SETB01", "Gasoline (all types)", 3),
    ("SEHE01", "Fuel oil", 3),
    ("SEHF", "Energy services", 2),
    ("SEHF01", "Electricity", 3),
    ("SEHF02", "Utility (piped) gas service", 3),
    ("SA0L1E", "All items less food and energy", 1),
    ("SACL1E", "Commodities less food and energy commodities", 2),
    ("SETA01", "New vehicles", 3),
    ("SETA02", "Used cars and trucks", 3),
    ("SAA", "Apparel", 3),
    ("SAM1", "Medical care commodities", 3),
    ("SASLE", "Services less energy services", 2),
    ("SAH1", "Shelter", 3),
    ("SAS4", "Transportation services", 3),
    ("SAM2", "Medical care services", 3),
]
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def bls(series, start, end):
    r = requests.post("https://api.bls.gov/publicAPI/v1/timeseries/data/",
                      json={"seriesid": series, "startyear": str(start), "endyear": str(end)}, timeout=60).json()
    if r.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(r.get("message"))
    out = {}
    for s in r["Results"]["series"]:
        out[s["seriesID"]] = {f"{d['year']}-{d['period'][1:]}": float(d["value"])
                              for d in s["data"] if d["period"].startswith("M") and d["period"] != "M13"
                              and d["value"] not in ("-", "")}
    return out


def month_key(text):
    m = re.match(r"([A-Za-z]+)\.?\s+(\d{4})", text.strip())
    if not m or m.group(1)[:3].lower() not in MONTHS:
        return None
    return f"{m.group(2)}-{MONTHS[m.group(1)[:3].lower()]:02d}"


def date_key(text):
    m = re.match(r"([A-Za-z]+)\.?\s+(\d{1,2}),\s+(\d{4})", text.strip())
    if not m or m.group(1)[:3].lower() not in MONTHS:
        return None
    return dt.date(int(m.group(3)), MONTHS[m.group(1)[:3].lower()], int(m.group(2))).isoformat()


def releases():
    try:
        r = crequests.get("https://www.bls.gov/schedule/news_release/cpi.htm", impersonate="chrome", timeout=60)
        r.raise_for_status()
        out = []
        for row in re.findall(r"<tr.*?</tr>", r.text, flags=re.S):
            c = cells(row)
            if len(c) >= 2 and month_key(c[0]) and date_key(c[1]):
                out.append({"month": month_key(c[0]), "date": date_key(c[1])})
        print(f"  release calendar: {len(out)} rows, {out[0]['month']} .. {out[-1]['month']}" if out else "  release calendar: none parsed")
        return out
    except Exception as e:  # the calendar is a convenience; the data still refreshes without it
        print(f"  release calendar failed: {e}")
        return []


def cpi():
    year = dt.date.today().year
    sa = bls([f"CUSR0000{code}" for code, _, _ in ITEMS], year - 9, year)
    nsa = bls([f"CUUR0000{code}" for code, _, _ in ITEMS], year - 9, year)
    months = sorted({m for s in list(sa.values()) + list(nsa.values()) for m in s})
    items = []
    for code, name, indent in ITEMS:
        s, n = sa.get(f"CUSR0000{code}", {}), nsa.get(f"CUUR0000{code}", {})
        items.append({"id": code, "name": name, "indent": indent,
                      "sa": [s.get(m) for m in months], "nsa": [n.get(m) for m in months]})
        if not s or not n:
            print(f"  {code} {name}: sa={len(s)} nsa={len(n)} months")
    print(f"  {len(items)} items, {months[0]} .. {months[-1]}")
    write("cpi.json", {"asof": now(), "source": "BLS CPI-U, U.S. city average", "months": months, "items": items, "releases": releases()})


# ---------------------------------------------------------------- main

PARTS = {"btc": btc, "stocks": stocks, "etf": etf, "cpi": cpi}

if __name__ == "__main__":
    wanted = [a for a in sys.argv[1:] if a in PARTS] or list(PARTS)
    failed = []
    for name in wanted:
        print(f"{name}:")
        try:
            PARTS[name]()
        except Exception as e:
            failed.append(name)
            print(f"  FAILED: {e}")
    if failed:
        sys.exit(f"failed: {', '.join(failed)}")
