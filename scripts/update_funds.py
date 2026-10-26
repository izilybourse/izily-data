#!/usr/bin/env python3
"""Met à jour data/funds.json pour la page « Supers investisseurs, politiciens & gérants de fonds ».
NON TESTÉ en conditions réelles : à lancer une première fois à la main (python scripts/update_funds.py) et à vérifier.

1) Fundsmith : dernier 13F-HR via SEC EDGAR (renseigner le CIK ci-dessous).
2) Autres sociétés : factsheet PDF mensuelle -> top 10 (regex "Nom ... 4,52 %"). Renseigner les URL dans FACTSHEETS.
   Les fiches dont l'URL est vide ou dont l'analyse échoue sont simplement ignorées : la page garde ses données intégrées.
"""
import os, io, json, re, sys, urllib.request, datetime, xml.etree.ElementTree as ET

UA = {"User-Agent": "izilyfire-data contact@izilyfire.fr"}  # la SEC exige un User-Agent identifiable
FUNDSMITH_CIK = ""  # ex. "0001234567" : à chercher sur sec.gov/cgi-bin/browse-edgar?company=fundsmith
FACTSHEETS = {      # id (identique à la page) -> URL du PDF mensuel le plus récent
    "comgest": "", "carmignac": "", "flossbach": "", "moneta": "",
}

def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()

def fundsmith_13f():
    if not FUNDSMITH_CIK: return None
    cik = FUNDSMITH_CIK.zfill(10)
    sub = json.loads(get(f"https://data.sec.gov/submissions/CIK{cik}.json"))["filings"]["recent"]
    i = next(k for k, f in enumerate(sub["form"]) if f == "13F-HR")
    acc = sub["accessionNumber"][i]; nodash = acc.replace("-", "")
    idx = json.loads(get(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{nodash}/index.json"))
    name = next(x["name"] for x in idx["directory"]["item"] if x["name"].lower().endswith(".xml") and "primary" not in x["name"].lower())
    root = ET.fromstring(get(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{nodash}/{name}"))
    agg, tot = {}, 0.0
    for e in root.iter():
        if e.tag.endswith("infoTable"):
            n = next(c.text for c in e if c.tag.endswith("nameOfIssuer"))
            v = float(next(c.text for c in e if c.tag.endswith("value")))
            agg[n] = agg.get(n, 0) + v; tot += v
    top = sorted(agg.items(), key=lambda x: -x[1])[:10]
    return {"id": "fundsmith", "asOf": sub["reportDate"][i], "holdingsLabel": "Top 10 des positions US (13F, % du portefeuille 13F)",
            "holdings": [{"name": n.title(), "ticker": "", "pct": round(100 * v / tot, 2)} for n, v in top]}

def factsheet(fid, url):
    import pdfplumber  # pip install pdfplumber
    with pdfplumber.open(io.BytesIO(get(url))) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages[:3])
    rows = re.findall(r"^\s*\d{0,2}\.?\s*([A-Za-zÀ-ÿ&.' \-]{3,45}?)\s+(\d{1,2}[.,]\d{1,2})\s*%", text, re.M)
    hold = [{"name": n.strip(), "ticker": "", "pct": float(p.replace(",", "."))} for n, p in rows][:10]
    return {"id": fid, "holdings": hold} if len(hold) >= 5 else None

def _f(s): return float(s.replace(",", "."))

def _rows(text):
    """Lignes 'libellé 18,7%' ; un libellé peut tenir sur plusieurs lignes."""
    rows, buf = [], ""
    for ln in (l.strip() for l in text.splitlines()):
        if not ln: continue
        m = re.match(r"^(.*?)\s*(-?\d{1,3},\d)\s*%$", ln)
        if m: rows.append([(buf + " " + m.group(1)).strip(), _f(m.group(2))]); buf = ""
        else: buf = (buf + " " + ln).strip()
    return rows

def _crop(page, x0, y0, x1, y1):
    w, h = page.width, page.height
    return (page.crop((x0 * w, y0 * h, x1 * w, y1 * h)).extract_text() or "")

def _col(o):
    c = o.get("non_stroking_color")
    if c is None: return None
    if isinstance(c, (int, float)): c = (c,)
    try: return tuple(round(float(x), 2) for x in c)
    except Exception: return None

def _shapes(sub):
    return [s for s in list(sub.rects) + list(sub.curves) if _col(s) is not None]

def _lines(words, tol=2.5):
    """Regroupe des mots en lignes (même hauteur)."""
    out = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if out and abs(out[-1]["top"] - w["top"]) <= tol: out[-1]["w"].append(w)
        else: out.append({"top": w["top"], "w": [w]})
    res = []
    for l in out:
        ws = sorted(l["w"], key=lambda w: w["x0"])
        res.append({"top": l["top"], "bottom": max(w["bottom"] for w in ws), "x0": ws[0]["x0"], "x1": ws[-1]["x1"], "text": " ".join(w["text"] for w in ws), "w": ws})
    return res

def _legend(sub, shapes, maxdim):
    """Associe chaque petit carré de légende (couleur) au texte situé à sa droite."""
    words, res = sub.extract_words(), {}
    for s in shapes:
        if max(s["width"], s["height"]) > maxdim: continue
        cy = (s["top"] + s["bottom"]) / 2
        ws = [w for w in words if w["x0"] >= s["x1"] - 1 and w["x0"] - s["x1"] < maxdim * 6 and abs((w["top"] + w["bottom"]) / 2 - cy) < max(s["height"], 6)]
        if ws: res.setdefault(_col(s), " ".join(w["text"] for w in sorted(ws, key=lambda w: w["x0"])))
    return res

CANON = [("< 500 M€", "<500"), ("500 M€ – 1 Md€", "500m"), ("1 – 10 Mds€", "1-10"), ("> 10 Mds€", ">10")]
def _canon(label):
    k = re.sub(r"\s+", "", label).lower().replace("–", "-").replace("—", "-")
    for out, key in CANON:
        if k.startswith(key): return out
    return None

def iam_geo(page):
    import math
    w, h = page.width, page.height
    sub = page.crop((0, .15 * h, .30 * w, .42 * h))
    shapes = _shapes(sub); legend = _legend(sub, shapes, .025 * w)
    wedges = [s for s in shapes if max(s["width"], s["height"]) > .025 * w and _col(s) in legend]
    if not (3 <= len(wedges) <= 10): return None
    x0 = min(s["x0"] for s in wedges); x1 = max(s["x1"] for s in wedges); t0 = min(s["top"] for s in wedges); t1 = max(s["bottom"] for s in wedges)
    cx, cy = (x0 + x1) / 2, (t0 + t1) / 2
    def ang(x, y): return math.atan2(-(y - cy), x - cx)
    def wedge_angle(s):
        pts = s.get("pts") or [(s["x0"], s["top"]), (s["x1"], s["bottom"])]
        mx = sum(p[0] for p in pts) / len(pts); my = sum(p[1] for p in pts) / len(pts)
        return ang(mx, my)
    labels = []
    for wd in sub.extract_words():
        m = re.match(r"^(\d{1,2},\d)\s*%?$", wd["text"])
        if m: labels.append((_f(m.group(1)), ang((wd["x0"] + wd["x1"]) / 2, (wd["top"] + wd["bottom"]) / 2)))
    if len(labels) != len(wedges): return None
    pairs = sorted(((abs(math.remainder(wedge_angle(s) - la, 2 * math.pi)), i, j) for i, s in enumerate(wedges) for j, (_, la) in enumerate(labels)))
    ui, uj, out = set(), set(), []
    for d, i, j in pairs:
        if i in ui or j in uj: continue
        ui.add(i); uj.add(j); out.append((legend[_col(wedges[i])], labels[j][0]))
    if len(out) != len(wedges) or not (99 <= sum(p for _, p in out) <= 101): return None
    small = sorted((x for x in shapes if max(x["width"], x["height"]) <= .025 * w and _col(x) in legend), key=lambda x: x["top"])
    order = {legend[_col(x)]: k for k, x in enumerate(small)}
    return sorted(([n.strip(), p] for n, p in out), key=lambda r: order.get(r[0], 99))

def iam_capbuckets(page):
    w, h = page.width, page.height
    sub = page.crop((0, .43 * h, .42 * w, .64 * h))
    shapes = _shapes(sub); legend = _legend(sub, shapes, .02 * w)
    segs = sorted((s for s in shapes if s["height"] > .03 * h and s["width"] > .003 * w), key=lambda s: s["x0"])
    if not (2 <= len(segs) <= 4): return None
    tot = sum(s["width"] for s in segs); words = sub.extract_words(); vals = {}
    for s in segs:
        lab = _canon(legend.get(_col(s), ""))
        if not lab: return None
        v = round(s["width"] / tot * 100, 1)
        for wd in words:
            m = re.match(r"^(\d{1,2},\d)\s*%?$", wd["text"]); xm = (wd["x0"] + wd["x1"]) / 2
            if m and s["x0"] - 1 <= xm <= s["x1"] + 1 and abs(_f(m.group(1)) - v) <= 1.0: v = _f(m.group(1))
        vals[lab] = v
    res = [[lab, vals.get(lab, 0)] for lab, _ in CANON]
    return res if 99 <= sum(v for _, v in res) <= 101 else None

ACR = {"ldc": "LDC", "tp": "TP", "nrj": "NRJ", "crit": "CRIT", "smcp": "SMCP", "gl": "GL", "icop": "ICOP", "nv": "NV", "ag": "AG", "asr": "ASR", "iss": "ISS", "sbm": "SBM", "scor": "SCOR", "dof": "DOF", "ssab": "SSAB", "caf": "CAF", "ksb": "KSB", "se": "SE", "ab": "AB"}
def _fixname(n): return " ".join(ACR.get(t.lower(), t) for t in n.split())

def iam_caps(page):
    w, h = page.width, page.height
    sub = page.crop((.74 * w, .04 * h, w, h))
    blocks = sorted((s for s in _shapes(sub) if s["height"] > .04 * h and s["width"] < .14 * w), key=lambda s: s["top"])
    if not (2 <= len(blocks) <= 4): return None
    right = max(b["x1"] for b in blocks); words = sub.extract_words(); out = []
    for b in blocks:
        lab = _canon(" ".join(wd["text"] for wd in words if b["x0"] <= wd["x0"] and wd["x1"] <= b["x1"] + 1 and b["top"] <= wd["top"] <= b["bottom"]))
        names = [_fixname(l["text"].strip()) for l in _lines([wd for wd in words if wd["x0"] >= right - 1 and b["top"] <= (wd["top"] + wd["bottom"]) / 2 <= b["bottom"]])]
        if not lab or not names: return None
        out.append([lab, names])
    n = sum(len(x[1]) for x in out)
    return out if 5 <= n <= 150 else None


def iam_report(page, label="Europe Mid"):
    """Relit les blocs de la page 2 du reporting. Chaque bloc est validé séparément :
    un bloc illisible est omis (le widget garde alors ses chiffres précédents et l'indique)."""
    out, full = {}, (page.extract_text() or "")
    try:  # secteurs
        t = _crop(page, .60, .09, .78, .60)
        t = t.split("Pondération", 1)[-1].split("Total", 1)[0]
        r = _rows(t)
        if 8 <= len(r) <= 25 and 97 <= sum(x[1] for x in r) <= 103: out["sectors"] = r
    except Exception as e: print("rapport : secteurs", e, file=sys.stderr)
    try:  # variations
        t = _crop(page, 0, .64, .60, 1.0)
        up, dn, cur = [], [], None
        for ln in t.splitlines():
            if re.match(r"\s*[ÉE]cart", ln): continue
            if re.search(r"sup[ée]rieures", ln, re.I): cur = up; continue
            if re.search(r"inf[ée]rieures", ln, re.I): cur = dn; continue
            m = re.match(r"^(.+?)\s+(-?\d{1,2},\d)\s*%\s+(.+)$", ln.strip())
            if m and cur is not None: cur.append([m.group(1).strip(), _f(m.group(2)), m.group(3).strip()])
        if 3 <= len(up) <= 8 and all(x[1] > 0 for x in up): out["up"] = up
        if 3 <= len(dn) <= 8 and all(x[1] < 0 for x in dn): out["down"] = dn
    except Exception as e: print("rapport : variations", e, file=sys.stderr)
    try:  # ratios
        flat = re.sub(r"\s+", " ", _crop(page, .29, .16, .60, .38))
        num = r"(\d+(?:,\d+)?)"
        def row(label, rx):
            m = re.search(rx + r"\D{0,3}?" + num + r" " + num + r" " + num + r"\s*%", flat, re.I)
            return [label, m.group(1), m.group(2), m.group(3) + " %"] if m else None
        a = row(label + " – médiane", r"m[ée]diane")
        b = row(label + " – moyenne pondérée", r"pond[ée]r[ée]e")
        c = row("Benchmark", r"Benchmark")
        g = re.search(r"cart[^\d-]*(-?\d+,\d)\s*%\s*(-?\d+,\d)\s*%\s*(\d+)\s*pts", flat, re.I)
        if a and b and c:
            out["ratios"] = {"cols": ["P/AN", "P/E", "Rdt."], "rows": [a, b, c]}
            if g: out["ratios"]["gap"] = ["Écart vs moyenne pondérée", g.group(1) + " %", g.group(2) + " %", g.group(3) + " pts"]
    except Exception as e: print("rapport : ratios", e, file=sys.stderr)
    try:  # capitalisation moyenne / médiane
        flat = re.sub(r"\s+", " ", full)
        for key, word in (("capAvg", "moyenne"), ("capMed", "m[ée]diane")):
            m = re.search(r"((?:\d{1,3}(?: \d{3})+)|\d{3,6}) ?M ?€ Capitalisation " + word, flat, re.I)
            if m:
                v = int(m.group(1).replace(" ", ""))
                if 300 <= v <= 200000: out[key] = f"{v:,}".replace(",", " ") + " M€"
    except Exception as e: print("rapport : capitalisations", e, file=sys.stderr)
    for key, fn in (("geo", iam_geo), ("capBuckets", iam_capbuckets), ("caps", iam_caps)):
        try:
            v = fn(page)
            if v: out[key] = v
        except Exception as e: print("rapport :", key, e, file=sys.stderr)
    if out:
        mois = ["janvier","février","mars","avril","mai","juin","juillet","août","septembre","octobre","novembre","décembre"]
        m = re.search(r"RAPPORT\s+([A-ZÉÛ]+)\s+(\d{4})", full)
        if m and m.group(1).lower() in mois: out["asOf"] = m.group(1).lower() + " " + m.group(2)
    return out or None


IAM_AJAX = "https://www.independance-am.com/wp-admin/admin-ajax.php"
# Les trois fonds d'Indépendance AM : page du fonds, mot-clé du PDF mensuel, identifiants du graphique (fonds / indice)
IAM_FUNDS = [
    {"id": "independance-am", "label": "Europe Mid", "page": "https://www.independance-am.com/nos-fonds/independance-europe-mid/",
     "slug": "europe-mid", "f1": "ie-europe-mid-i", "f2": "stoxx-europe-mid"},
    {"id": "independance-europe-small", "label": "Europe Small", "page": "https://www.independance-am.com/nos-fonds/europe-small/",
     "slug": "europe-small", "f1": "ie-europe-i", "f2": "stoxx-europe"},
    {"id": "independance-france-small-mid", "label": "France Small & Mid", "page": "https://www.independance-am.com/nos-fonds/france-small/",
     "slug": "france-small-mid", "f1": "ie-x", "f2": "cac-midsmall-nr"},
]

def independance(cfg):
    """Retrouve seul le dernier reporting mensuel du fonds sur sa page, puis lit son top 10 et son reporting détaillé."""
    import pdfplumber
    html = get(cfg["page"]).decode("utf8", "ignore")
    links = re.findall(r'href=["\']([^"\']*/wp-content/uploads/(\d{4})/(\d{2})/([^"\'/]*reporting[^"\'/]*' + re.escape(cfg["slug"]) + r'[^"\'/]*\.pdf))', html, re.I)
    if not links: return None
    def key(l):
        m = re.match(r"(\d{6,8})", l[3]); return (l[1], l[2], m.group(1)[-6:] if m else "")
    url, _, _, name = max(links, key=key)
    with pdfplumber.open(io.BytesIO(get(url))) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages[:3])
        rep_page = next((pg for pg in pdf.pages[:3] if "Principaux investissements" in (pg.extract_text() or "")), None)
        report = iam_report(rep_page, cfg["label"]) if rep_page else None
    sec = text.split("Principaux investissements", 1)[-1][:900]
    rows = re.findall(r"([A-Za-zÀ-ÿ&.' \-]{2,40}?)\s+(\d{1,2},\d)\s*%", sec)
    hold = [{"name": _fixname(n.strip()), "ticker": "", "pct": float(p.replace(",", "."))} for n, p in rows][:10]
    tot = sum(h["pct"] for h in hold)
    out = {"id": cfg["id"]}
    if 8 <= len(hold) <= 10 and 20 <= tot <= 45 and all(0 < h["pct"] < 15 for h in hold):
        out.update({"holdings": hold, "top10": round(tot, 1), "holdingsLabel": "Top 10 (reporting mensuel)"})
    else:
        print(cfg["id"], ": top 10 illisible, données intégrées conservées", file=sys.stderr)
    m = re.search(r"Actif[^\n]{0,40}?(\d[\d\s]{0,7})\s*M\s*€", text, re.I)
    if m: out["aum"] = re.sub(r"\s+", " ", m.group(1).strip()) + " M€"
    m = re.search(r"(\d{2,3})\s*(?:titres|lignes)", text, re.I) or re.search(r"Nombre de titres\D{0,15}(\d{2,3})", text, re.I)
    if m: out["nbHoldings"] = int(m.group(1))
    d = re.match(r"(\d{2})(\d{2})(\d{2})", name)
    if d: out["asOf"] = f"{d.group(3)}/{d.group(2)}/20{d.group(1)}"
    if report: out["report"] = report
    return out if len(out) > 1 else None

def _iam_chart(cfg, date_filter):
    """Série 'base 100' du fonds et de son indice, telle qu'affichée sur la page du fonds (même appel que le site)."""
    import urllib.parse
    body = urllib.parse.urlencode({"action": "load_chart", "fund_1": cfg["f1"], "fund_2": cfg["f2"], "date_filter": date_filter}).encode()
    req = urllib.request.Request(IAM_AJAX, data=body, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://www.independance-am.com", "Referer": cfg["page"], "Accept": "application/json, text/javascript, */*; q=0.01"})
    j = json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf8"))
    if not j.get("success"): raise ValueError("réponse négative du site")
    d = j["data"]; st = json.loads(d["stats"]); a, b = st[cfg["f1"]], st[cfg["f2"]]
    if len(a) != len(b) or len(a) < 100: raise ValueError("série trop courte")
    pts = [[round(x[0] / 86400000), x[1], y[1]] for x, y in zip(a, b)]
    if any(pts[i][0] >= pts[i + 1][0] for i in range(len(pts) - 1)): raise ValueError("dates non croissantes")
    if not all(20 < v < 30000 for q in pts for v in q[1:]): raise ValueError("valeurs aberrantes")
    if len(pts) > 600:  # historique long : on allège (le dernier point est toujours conservé)
        step = len(pts) / 330; pts = [pts[int(k * step)] for k in range(330)] + [pts[-1]]
    nb = lambda t: re.sub(r"(&nbsp;|\xa0)", " ", str(t))
    def pk(x):
        o = {"cum": nb(x["cumulativePerformance"]), "ann": nb(x["annualizedPerformance"]), "vl": x["vl"], "from": x["fromDate"], "to": x["toDate"]}
        if x.get("YTDPerformance"): o["ytd"] = nb(x["YTDPerformance"])
        return o
    return {"period": d.get("period"), "f": pk(d["array_stats"]["fund1"]), "b": pk(d["array_stats"]["fund2"]), "p": pts}

def independance_perf(cfg):
    out = {}
    for key, flt in (("all", "all"), ("y1", "1")):
        try: out[key] = _iam_chart(cfg, flt)
        except Exception as e: print("independance perf", cfg["id"], key, ":", e, file=sys.stderr)
    return {"id": cfg["id"], "perf": out} if out else None

def main():
    funds = []
    for job in [fundsmith_13f] + [(lambda c=c: independance(c)) for c in IAM_FUNDS] + [(lambda c=c: independance_perf(c)) for c in IAM_FUNDS] + [lambda f=f, u=u: factsheet(f, u) for f, u in FACTSHEETS.items() if u]:
        try:
            r = job()
            if r: funds.append(r)
        except Exception as e:
            print("ignoré :", e, file=sys.stderr)
    out = {"generatedAt": datetime.datetime.utcnow().isoformat() + "Z", "funds": funds}
    json.dump(out, open("data/funds.json", "w", encoding="utf8"), ensure_ascii=False, indent=1)
    print(len(funds), "fonds mis à jour")

main()
