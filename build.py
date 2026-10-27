"""PineLab dashboard builder (stdlib only). Reads ledgers checked out under src/, writes site/index.html + site/data.json.

  src/sig/   signal-ledger branch (gold night signal: PAPER_RULES / POSTED / NO_SIGNAL ... per night)
  src/tsy/   tsy-ledger branch (d-040 month-end Treasury paper)
  src/demo/  demo-ledger branch (MT5 DEMO executions: gold/, index/, heartbeat.json)
  cache/gc_60m.json  accumulated Yahoo GC=F 60m opens (NY wall hour -> open), committed by the workflow
"""
import os, sys, json, glob, math, statistics, urllib.request, datetime as dt, html
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York"); CZ = ZoneInfo("Europe/Prague"); UTC = dt.timezone.utc
ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src"); OUT = os.path.join(ROOT, "site"); CACHE = os.path.join(ROOT, "cache", "gc_60m.json")
EXITS = [3, 1, 23, 20]               # v5 exit_idx -> NY hour (as in the live code)
CFD_COST = 0.50                      # USD/oz round trip, PurpleTrading CFD main cost (M4/M10)
SWAP = 0.59                          # USD/oz per 17:00 NY rollover held (Wed x3) - only the 22:00 comparison pays it
# M10 (2026-10-27): paper entry moved 16:00 NY -> broker reopen 18:00 NY (same as demo), CFD costs, no swap.
PAPER_START = dt.date(2026, 10, 5)   # first night with R19/R21 cloud ledger
ARMS = [("v5", "v5 (hlavní model)", "#4f8cff"), ("R043", "R043 (opatrnější v rizikových nocích)", "#a879ff"),
        ("R045", "R045 (mírně opatrnější)", "#ff8bd0"), ("R20C", "R20C (odpolední tilt)", "#3fd0c9")]


def jload(p, default=None):
    try:
        with open(p, encoding="utf-8-sig") as f: return json.load(f)
    except Exception:
        return default

def jsonl(p):
    out = []
    if not os.path.exists(p): return out
    for line in open(p, encoding="utf-8-sig"):
        line = line.strip()
        if line:
            try: out.append(json.loads(line))
            except Exception: pass
    return out

def now_utc(): return dt.datetime.now(UTC)


# ------------------------------------------------------------------ gold prices (Yahoo GC=F 60m, accumulated)
def gold_prices():
    cache = jload(CACHE, {}) or {}
    try:
        req = urllib.request.Request("https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=60m&range=1mo",
                                     headers={"User-Agent": "Mozilla/5.0"})
        r = json.load(urllib.request.urlopen(req, timeout=30))["chart"]["result"][0]
        for t, o in zip(r["timestamp"], r["indicators"]["quote"][0]["open"]):
            if o is None: continue
            k = dt.datetime.fromtimestamp(t, NY).replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H")
            cache[k] = o
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump(dict(sorted(cache.items())), open(CACHE, "w"), indent=0)
        ok = True
    except Exception as e:
        print("yahoo fail:", e); ok = False
    return cache, ok

DOW = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"]
def cz_short(x): return f"{DOW[x.weekday()]} {x:%H:%M}"

def hk(d, h): return dt.datetime(d.year, d.month, d.day, h, tzinfo=NY).strftime("%Y-%m-%dT%H")

def reopen(d):
    x = dt.datetime(d.year, d.month, d.day, 18, tzinfo=NY)
    return x + dt.timedelta(days=2) if d.weekday() == 4 else x

def exit_dt(d, h):
    ro = reopen(d); x = ro.replace(hour=h)
    return x if x > ro else x + dt.timedelta(days=1)


# ------------------------------------------------------------------ signal ledger -> paper nights
def signal_nights():
    nights = {}
    for p in sorted(glob.glob(os.path.join(SRC, "sig", "2*", "*.json"))):
        r = jload(p)
        if not r: continue
        day = os.path.basename(os.path.dirname(p)); D = dt.date(int(day[:4]), int(day[4:6]), int(day[6:8]))
        n = nights.setdefault(D, {"D": D, "events": [], "paper": None, "posted": None})
        n["events"].append(r.get("event"))
        if r.get("event") == "PAPER_RULES" and (r.get("paper") or {}).get("ok"):
            if not r.get("dry_run") and not r.get("manual"):
                n["paper"] = r
        if r.get("event") == "POSTED": n["posted"] = r
    return nights

def paper_book(nights, px):
    rows = []
    for D in sorted(nights):
        n = nights[D]
        if D < PAPER_START: continue
        P = (n["paper"] or {}).get("paper")
        if not P:
            rows.append({"D": str(D), "state": "NO_SIGNAL" if "NO_SIGNAL_POSTED" in n["events"] or "NO_SIGNAL" in n["events"] else "BEZ ZÁZNAMU"})
            continue
        h = EXITS[int(P.get("exit_idx", 0))]; xd = exit_dt(D, h)
        ro = reopen(D); E = px.get(ro.strftime("%Y-%m-%dT%H")); E16 = px.get(hk(D, 16))
        X = px.get(xd.strftime("%Y-%m-%dT%H")); G3 = px.get(exit_dt(D, 3).strftime("%Y-%m-%dT%H"))
        row = {"D": str(D), "dow": DOW[D.weekday()], "exit_ny": h, "exit_cz": cz_short(xd.astimezone(CZ)), "entry_cz": cz_short(ro.astimezone(CZ)),
               "dec": P.get("dec_v5"), "paka": {}, "ret": {}}
        pk = {"v5": P.get("v5_paka")}
        for a in ("R043", "R045", "R20C"):
            pk[a] = (P.get(a) or {}).get("paka")
        row["paka"] = pk
        if E and X:
            row["state"] = "UZAVŘENO"
            for a, p in pk.items():
                if p is None: continue
                row["ret"][a] = p * (X / E - 1) - p * CFD_COST / E
            if G3: row["ret"]["gold"] = G3 / E - 1
            if E16 and pk.get("v5") is not None:
                roll = 3 if D.weekday() == 2 else 1; p = pk["v5"]
                row["ret"]["v5_22"] = p * (X / E16 - 1) - p * CFD_COST / E16 - p * SWAP * roll / E16
        else:
            row["state"] = "OTEVŘENO" if xd > now_utc() else "CHYBÍ CENY"
        rows.append(row)
    return rows

def curve(rets):
    eq, peak, dd, pts = 1.0, 1.0, 0.0, []
    for d, r in rets:
        eq *= 1 + r; peak = max(peak, eq); dd = min(dd, eq / peak - 1); pts.append([d, round((eq - 1) * 100, 4)])
    return pts, (eq - 1) * 100, dd * 100

def stats_from(rets):
    xs = [r for _, r in rets]
    pts, cum, dd = curve(rets)
    return {"n": len(xs), "cum_pct": cum, "maxdd_pct": dd, "win_pct": (100 * sum(x > 0 for x in xs) / max(1, sum(x != 0 for x in xs))) if any(x != 0 for x in xs) else None, "active": sum(x != 0 for x in xs),
            "last_pct": xs[-1] * 100 if xs else None, "avg_bp": statistics.mean(xs) * 1e4 if xs else None, "curve": pts}


# ------------------------------------------------------------------ demo executions
def demo_gold():
    base = os.path.join(SRC, "demo", "gold")
    recs = jsonl(os.path.join(base, "trades.jsonl"))
    states = [jload(p) for p in sorted(glob.glob(os.path.join(base, "state", "N-*.json")))]
    closed = [r for r in recs if r.get("status") == "CLOSED"]
    eq0 = None
    rets, rows = [], []
    for r in closed:
        eqc = (r.get("sizing") or {}).get("eq_czk")
        eq0 = eq0 or eqc
        rets.append((r["D"], r.get("net_czk", 0.0) / eqc if eqc else 0.0))
        rows.append({"D": r["D"], "lots": (r.get("sizing") or {}).get("lots"), "buy": (r.get("entry") or {}).get("fill"),
                     "sell": (r.get("exit") or {}).get("fill"), "net_czk": r.get("net_czk"), "cost": r.get("cost_rt_usd_oz"),
                     "exit_ny": r.get("exit_hour_ny")})
    costs = [r["cost_rt_usd_oz"] for r in closed if r.get("cost_rt_usd_oz") is not None]
    cnt = {k: sum(r.get("status") == k for r in recs) for k in ("MISSED", "PC_OFF", "FLAT", "NO_SIGNAL")}
    st = stats_from(rets)
    st.update(rows=rows[-30:][::-1], counts=cnt, net_czk=sum(r.get("net_czk", 0.0) for r in closed),
              cost_median=statistics.median(costs) if costs else None, cost_mean=statistics.mean(costs) if costs else None,
              open=next((s for s in reversed(states) if s and s.get("phase") not in ("DONE",)), None))
    if st["open"]:
        o = st["open"]; st["open"] = {"D": o.get("D"), "phase": o.get("phase"), "exit_ny": o.get("exit_ny"),
                                      "entry": (o.get("entry") or {}).get("fill"), "lots": (o.get("sizing") or {}).get("lots")}
    return st

def demo_index():
    base = os.path.join(SRC, "demo", "index")
    recs = jsonl(os.path.join(base, "index_trades.jsonl"))
    states = [jload(p) for p in sorted(glob.glob(os.path.join(base, "state", "I-*.json")))]
    closed = [r for r in recs if r.get("status") == "CLOSED"]
    C0 = 40000.0
    by = {}
    for r in closed:
        b = by.setdefault(r["entry_date"], {"D": r["entry_date"], "net_czk": 0.0, "legs": {}})
        b["net_czk"] += r.get("net_czk", 0.0)
        b["legs"][r["sym"]] = {"cost_bp": r.get("cost_rt_bp"), "gross_bp": r.get("gross_bp"), "shortfall_bp": r.get("shortfall_bp")}
    cap, rets = C0, []
    for D in sorted(by):
        rets.append((D, by[D]["net_czk"] / cap)); cap += by[D]["net_czk"]
    def med(sym, f):
        xs = [r[f] for r in closed if r.get("sym") == sym and r.get(f) is not None]
        return (statistics.median(xs) if xs else None, statistics.mean(xs) if xs else None, len(xs))
    nights_total = len({r.get("entry_date") for r in recs if r.get("entry_date")})
    bad = sum(r.get("status") in ("MISSED", "INCIDENT", "BROKER_CLOSED", "REFUSED") for r in recs)
    st = stats_from(rets)
    st.update(capital_czk=cap, net_czk=cap - C0, rows=[by[d] for d in sorted(by)][-30:][::-1],
              cost={s: med(s, "cost_rt_bp") for s in ("US500_ecn", "US100_ecn")},
              shortfall={s: med(s, "shortfall_bp") for s in ("US500_ecn", "US100_ecn")},
              swap_incidents=sum(bool(r.get("swap_incident")) for r in closed),
              counts={k: sum(r.get("status") == k for r in recs) for k in ("MISSED", "BROKER_CLOSED", "INCIDENT", "REFUSED", "PC_OFF", "FLAT_HOLIDAY")},
              bad_share=(100 * bad / max(1, len(recs))) if recs else None, nights_seen=nights_total)
    o = next((s for s in reversed(states) if s), None)
    if o:
        st["open"] = {"key": o.get("key"), "entry_date": o.get("entry_date"), "exit_date": o.get("exit_date"),
                      "legs": {s: (v or {}).get("phase") for s, v in (o.get("legs") or {}).items()}}
    return st

def d040():
    res = []
    for p in sorted(glob.glob(os.path.join(SRC, "tsy", "tsy", "*_result.json"))):
        r = jload(p)
        if r and "net" in r: res.append((r.get("window"), r["net"]))
    st = stats_from(res)
    buys = sorted(os.path.basename(p)[:7] for p in glob.glob(os.path.join(SRC, "tsy", "tsy", "*_buy.json")))
    sells = sorted(os.path.basename(p)[:7] for p in glob.glob(os.path.join(SRC, "tsy", "tsy", "*_sell.json")))
    st["windows"] = [{"window": w, "net_pct": n * 100} for w, n in res][::-1]
    st["open_window"] = next((b for b in reversed(buys) if b not in [w for w, _ in res]), None)
    st["open_sold"] = st["open_window"] in sells if st["open_window"] else False
    return st


# ------------------------------------------------------------------ build
def main():
    px, px_ok = gold_prices()
    nights = signal_nights()
    book = paper_book(nights, px)
    paper = {}
    for a, name, col in ARMS + [("gold", "Zlato 1× noc 18→03 NY (srovnání, bez nákladů)", "#c9a227"), ("v5_22", "v5 s nákupem 22:00 (CFD se swapem, srovnání)", "#7d8597")]:
        rets = [(r["D"], r["ret"][a]) for r in book if a in r.get("ret", {})]
        s = stats_from(rets); s.update(name=name, color=col); paper[a] = s
    last = next((r for r in reversed(book) if r.get("paka")), None)
    hb = jload(os.path.join(SRC, "demo", "heartbeat.json"), {}) or {}
    cloud = jload(os.path.join(SRC, "demo", "cloud_heartbeat.json"), {}) or {}
    data = {
        "built_utc": now_utc().isoformat(timespec="seconds"),
        "built_cz": now_utc().astimezone(CZ).strftime("%d.%m.%Y %H:%M"),
        "prices_ok": px_ok, "paper": paper, "book": book[::-1][:40], "tonight": last,
        "demo_gold": demo_gold(), "demo_index": demo_index(), "d040": d040(),
        "heartbeat": hb, "cloud": cloud, "m10": jload(os.path.join(ROOT, "research", "m10.json")),
        "last_signal": max((str(D) for D, n in nights.items() if n["posted"]), default=None),
    }
    os.makedirs(OUT, exist_ok=True)
    json.dump(data, open(os.path.join(OUT, "data.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    tpl = open(os.path.join(ROOT, "template.html"), encoding="utf-8").read()
    page = tpl.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/"))
    open(os.path.join(OUT, "index.html"), "w", encoding="utf-8").write(page)
    open(os.path.join(OUT, ".nojekyll"), "w").write("")
    print("built", data["built_cz"], "paper nights", paper["v5"]["n"], "demo gold", data["demo_gold"]["n"],
          "demo index", data["demo_index"]["n"], "d040", data["d040"]["n"])

if __name__ == "__main__":
    main()
