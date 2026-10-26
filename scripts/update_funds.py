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


IAM_SECTORS = ["Ingénierie & Construction", "Biens d'équipement", "Financières", "Services aux entreprises", "Énergie", "Biens de consommation", "Défense", "Matériaux", "IT / R&D ext.", "Équipements & Services para-énergétiques", "Équipementiers auto.", "Médias & Communication", "Immobilier", "Transport & Logistique", "Métaux & minerais", "Santé", "Distribution spécialisée", "Divers", "Agroalimentaire", "Équipements et solutions IT"]

def _iam_sectors(rows):
    """Les libellés sur 2 lignes sont lus décalés par l'extraction PDF (« Ingénierie & » / « Construction Biens d'équipement »).
    On recolle tous les fragments puis on les redécoupe avec la liste des secteurs connus ; si le découpage ne tombe pas
    exactement sur le bon nombre de lignes, le bloc est ignoré (la page garde ses chiffres précédents)."""
    import unicodedata
    def nz(x):
        x = unicodedata.normalize("NFD", x); x = "".join(c for c in x if unicodedata.category(c) != "Mn").lower()
        return re.sub(r"\s+", " ", re.sub(r"-\s+", "-", x)).strip()
    alias = {"Bien de consommation": "Biens de consommation", "Équipementiers auto": "Équipementiers auto.", "Equipementiers": "Équipementiers auto."}
    known = sorted([(nz(k), k) for k in IAM_SECTORS] + [(nz(a), v) for a, v in alias.items()], key=lambda t: -len(t[0]))
    stream = nz(" ".join(r[0] for r in rows)); n = len(rows)
    def seg(pos, acc):
        if pos >= len(stream): return acc if len(acc) == n else None
        if len(acc) >= n: return None
        if len(acc) == n - 1:   # dernier libellé coupé par le cadrage (« Equipements et » pour « Equipements et solutions IT »)
            rest = stream[pos:]
            cand = [lab for key, lab in known if key.startswith(rest) and len(rest) >= 8]
            if len(set(cand)) == 1: return acc + [cand[0]]
        for key, lab in known:
            if stream.startswith(key, pos) and (pos + len(key) == len(stream) or stream[pos + len(key)] == " "):
                r = seg(pos + len(key) + 1, acc + [lab])
                if r: return r
        return None
    labs = seg(0, [])
    return [[l, r[1]] for l, r in zip(labs, rows)] if labs else None

def iam_report(page, label="Europe Mid"):
    """Relit les blocs de la page 2 du reporting. Chaque bloc est validé séparément :
    un bloc illisible est omis (le widget garde alors ses chiffres précédents et l'indique)."""
    out, full = {}, (page.extract_text() or "")
    try:  # secteurs
        t = _crop(page, .60, .09, .78, .60)
        t = t.split("Pondération", 1)[-1].split("Total", 1)[0]
        r = _iam_sectors(_rows(t))
        if r and 8 <= len(r) <= 25 and 97 <= sum(x[1] for x in r) <= 103: out["sectors"] = r
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


# ---------------------------------------------------------------- Sunny AM (Sunny Managers, part I)
SUNNY_PAGE = "https://www.sunny-am.com/fonds/sunny-managers"
SUNNY_ISIN = "FR0013284544"

def _sunny_html():
    return get(SUNNY_PAGE).decode("utf8", "ignore")

def _sunny_block(html, marker):
    """Script inline contenant les données d'un graphique du fonds."""
    for blk in re.split(r"<script", html):
        if marker in blk: return blk
    return ""

def _sunny_arr(blk, name):
    m = re.search(r"(?:const|var|let)\s+" + name + r"\s*=\s*\[(.*?)\]", blk, re.S)
    return [float(x) for x in re.findall(r'"(-?\d+(?:\.\d+)?)"', m.group(1))] if m else []

def sunny_perf():
    import datetime as dt
    html = _sunny_html()
    vl = _sunny_block(html, "chart-vl-" + SUNNY_ISIN)
    m = re.search(r"chart-vl-" + SUNNY_ISIN + r"'.*?labels:\s*\[(.*?)\]", vl, re.S)
    days = [round(dt.datetime(int(y), int(mo), int(d)).replace(tzinfo=dt.timezone.utc).timestamp() / 86400)
            for d, mo, y in re.findall(r'"(\d{2})\\?/(\d{2})\\?/(\d{4})"', m.group(1))] if m else []
    blk = _sunny_block(html, "'chart-perf-btd-" + SUNNY_ISIN + "'")
    f, b = _sunny_arr(blk, "VLbtd_data"), _sunny_arr(blk, "VLbtdIND_data")
    if not (len(days) == len(f) == len(b) >= 200): raise ValueError("séries Sunny illisibles")
    if any(days[i] >= days[i + 1] for i in range(len(days) - 1)): raise ValueError("dates non croissantes")
    def dec(pts, n):
        if len(pts) <= n: return pts
        st = len(pts) / n; return [pts[int(k * st)] for k in range(n)] + [pts[-1]]
    allp = [[d, round(x, 2), round(y, 2)] for d, x, y in zip(days, f, b)]
    f1, b1 = _sunny_arr(blk, "VLp1an_data"), _sunny_arr(blk, "VLp1anIND_data")
    y1 = []
    if f1 and len(f1) == len(b1) <= len(days):
        d1 = days[-len(f1):]; y1 = [[d, round(x / f1[0] * 100, 2), round(y / b1[0] * 100, 2)] for d, x, y in zip(d1, f1, b1)]
    fm = lambda v: ("%.2f" % v).replace(".", ",") + " %"
    yrs = max((days[-1] - days[0]) / 365.25, 0.1)
    ann = lambda c: ((1 + c / 100) ** (1 / yrs) - 1) * 100
    cf, cb = f[-1] - 100, b[-1] - 100
    iso = lambda d: dt.datetime.fromtimestamp(d * 86400, dt.timezone.utc).strftime("%Y-%m-%d")
    out = {"all": {"period": "origine", "f": {"cum": fm(cf), "ann": fm(ann(cf)), "vl": "", "from": iso(days[0]), "to": iso(days[-1])},
                   "b": {"cum": fm(cb), "ann": fm(ann(cb)), "vl": "", "from": iso(days[0]), "to": iso(days[-1])}, "p": dec(allp, 330)}}
    ye, yb = _sunny_arr(blk, "VLeytd_data"), _sunny_arr(blk, "VLeytdIND_data")
    if ye and yb:
        out["all"]["f"]["ytd"] = fm(ye[-1] / ye[0] * 100 - 100); out["all"]["b"]["ytd"] = fm(yb[-1] / yb[0] * 100 - 100)
    if y1:
        c1, c2 = y1[-1][1] - 100, y1[-1][2] - 100
        out["y1"] = {"period": "1 an", "f": {"cum": fm(c1), "ann": fm(c1), "vl": "", "from": iso(y1[0][0]), "to": iso(y1[-1][0])},
                     "b": {"cum": fm(c2), "ann": fm(c2), "vl": "", "from": iso(y1[0][0]), "to": iso(y1[-1][0])}, "p": dec(y1, 250)}
    return {"id": "sunny", "perf": out}

def sunny():
    """Dernier reporting mensuel 'Sunny Managers – part I' : KPI, top 10, secteurs, performances."""
    import pdfplumber
    html = _sunny_html()
    links = re.findall(r'href=["\']([^"\']*Reporting/SUNNY(?:%20| )MANAGERS(?:%20| )-(?:%20| )PART(?:%20| )I(?:%20| )-(?:%20| )' + SUNNY_ISIN + r'[^"\']*\.pdf)', html, re.I)
    if not links: return None
    url = max(links, key=lambda u: re.findall(r"(\d{2}-\d{2}-\d{2})\.pdf", u)[-1:] or [""])
    if url.startswith("/"): url = "https://www.sunny-am.com" + url
    with pdfplumber.open(io.BytesIO(get(url.replace(" ", "%20")))) as pdf:
        p1, p2 = pdf.pages[0], pdf.pages[1]
        t1, t2 = p1.extract_text() or "", p2.extract_text() or ""
        words2 = p2.extract_words()
    out = {"id": "sunny"}
    m = re.search(r"AUM\s+(\d[\d\s,]*)\s*M€", t1)
    if m: out["aum"] = m.group(1).strip() + " M€"
    m = re.search(r"Nombre de titres en portefeuille\s+(\d{2,3})", t1)
    if m: out["nbHoldings"] = int(m.group(1))
    m = re.search(r"Poids top 10\s+(\d{2},\d{1,2})\s*%", t1)
    if m: out["top10"] = _f(m.group(1))
    m = re.search(r"Rapport Mensuel\s*-\s*([a-zéû]+)\s+(\d{4})", t2, re.I)
    mois = {"janvier": "01", "février": "02", "mars": "03", "avril": "04", "mai": "05", "juin": "06", "juillet": "07", "août": "08", "septembre": "09", "octobre": "10", "novembre": "11", "décembre": "12"}
    rep = {}
    if m and m.group(1).lower() in mois:
        import calendar
        mo, yr = int(mois[m.group(1).lower()]), int(m.group(2))
        out["asOf"] = f"{calendar.monthrange(yr, mo)[1]:02d}/{mo:02d}/{yr}"; rep["asOf"] = m.group(1).lower() + " " + m.group(2)
    sec = t2.split("Principales positions", 1)[-1]
    rows = re.findall(r"^([A-Z0-9&.'\- ]{3,40}?)\s+(\d{1,2},\d{2})%\s*$", sec, re.M)
    hold = [{"name": _fixname(n.strip().title()), "ticker": "", "pct": _f(p)} for n, p in rows][:10]
    tot = sum(h["pct"] for h in hold)
    if 8 <= len(hold) <= 10 and 20 <= tot <= 60: out["holdings"] = hold; out["holdingsLabel"] = "Top 10 (reporting mensuel, part I)"
    # secteurs : valeurs (x croissant) <-> libellés (x croissant)
    vals = sorted([w for w in words2 if re.fullmatch(r"\d{1,2},\d%", w["text"]) and w["top"] < 340 and w["x0"] > 270], key=lambda w: w["x0"])
    labs = sorted([w for w in words2 if w["x0"] > 200 and w["top"] > 285 and w["top"] < 345 and re.search(r"[A-Za-zéÉ]", w["text"]) and not w["text"].endswith("%")], key=lambda w: w["x0"])
    if len(vals) >= 5 and len(vals) == len(labs):
        sectors = [[l["text"], _f(v["text"].rstrip("%"))] for l, v in zip(labs, vals)]
        if 97 <= sum(x[1] for x in sectors) <= 103: rep["sectors"] = sectors
    mm = re.search(r"France\s+(\d{2,3},\d)\s*%", t2) or re.search(r"(\d{2,3},\d)%\s*France", t2)
    if mm and abs(_f(mm.group(1)) - 100) < 0.5: rep["geo"] = [["France", 100.0]]
    # performances cumulées
    prow = re.findall(r"^(YTD|1 mois|3 mois|6 mois|1 an|3 ans|5 ans|Origine)\s+(-?\d+,\d+)%\s+(-?\d+,\d+)%\s+(-?\d+,\d+)%", t1, re.M)
    if len(prow) == 8:
        lab = {"YTD": "Depuis le 1er janv.", "Origine": "Depuis la création"}
        rep["tables"] = [{"title": "Performances cumulées", "cols": ["Fonds", "Indice", "Écart"], "rows": [[lab.get(a, a)] + [x.replace("%", " %") for x in (b, c, d)] for a, b, c, d in prow],
                          "note": "Indice : Bloomberg France Mid 60+ Net Return depuis le 03/10/2025 ; auparavant CAC Mid & Small (dividendes réinvestis)."}]
    if rep: out["report"] = rep
    return out if len(out) > 1 else None


# ---------------------------------------------------------------- HMG Finance (HMG Découvertes, part D)
HMG_PAGE = "https://www.hmgfinance.com/opcvm/hmg-decouvertes-d/"
HMG_AJAX = "https://www.hmgfinance.com/wp-admin/admin-ajax.php"

def hmg():
    """Dernier reporting mensuel 'Découvertes D' : encours, lignes, top 10, secteurs, capitalisations."""
    import pdfplumber
    html = get(HMG_PAGE).decode("utf8", "ignore")
    cand = []
    for u in re.findall(r'href=["\']([^"\']*[Rr]eporting-[Dd]ecouvertes-[Dd]-[^"\']*\.pdf)', html):
        m = re.search(r"-(\d{2})-(\d{4})(?:-\d+)?\.pdf$", u) or re.search(r"-(\d{4})-(\d{2})\.pdf$", u)
        if not m: continue
        a, b = m.groups(); mo, yr = (a, b) if len(b) == 4 else (b, a)
        cand.append(((int(yr), int(mo)), u, int(mo), int(yr)))
    if not cand: return None
    _, url, mo, yr = max(cand)
    with pdfplumber.open(io.BytesIO(get(url))) as pdf:
        txt = "\n".join((p.extract_text() or "") for p in pdf.pages[:3])
    out = {"id": "hmg"}
    import calendar
    out["asOf"] = f"{calendar.monthrange(yr, mo)[1]:02d}/{mo:02d}/{yr}"
    m = re.search(r"Actif net[^\d]{0,30}([\d\s]{6,15}[,.]?\d*)", txt, re.I)
    if m:
        v = float(re.sub(r"\s", "", m.group(1)).replace(",", ".") or 0)
        if 1e6 < v < 5e10: out["aum"] = f"{v / 1e6:.1f}".replace(".", ",") + " M€"
    m = re.search(r"Nombre de (?:lignes|titres|valeurs)[^\d]{0,20}(\d{2,3})", txt, re.I)
    if m: out["nbHoldings"] = int(m.group(1))
    sec = re.split(r"10 premi[èe]res? (?:lignes|positions|valeurs)|Principales (?:lignes|positions)", txt, maxsplit=1, flags=re.I)
    rows = re.findall(r"^([A-Za-zÀ-ÿ0-9&.' \-]{3,40}?)\s+(\d{1,2}[,.]\d)\s*%\s*$", sec[-1] if len(sec) > 1 else "", re.M)
    hold = [{"name": _fixname(n.strip()), "ticker": "", "pct": _f(p.replace(".", ","))} for n, p in rows][:10]
    tot = sum(h["pct"] for h in hold)
    if len(hold) == 10 and 20 <= tot <= 70:
        out["holdings"] = hold; out["top10"] = round(tot, 1); out["holdingsLabel"] = "Top 10 (reporting mensuel, part D)"
    rep = {}
    mois = ["janvier","février","mars","avril","mai","juin","juillet","août","septembre","octobre","novembre","décembre"]
    rep["asOf"] = mois[mo - 1] + " " + str(yr)
    SEC = ["Industrie", "Matériaux", "Finance", "Technologie", "Conso. discrétionnaire", "Conso. de base", "Santé", "Services aux collectivités", "Énergie"]
    names = {"Industrials": "Industrie", "Industrie": "Industrie", "Materials": "Matériaux", "Matériaux": "Matériaux", "Financials": "Finance", "Finance": "Finance", "Technology": "Technologie", "Technologie": "Technologie",
             "Consommation discrétionnaire": "Conso. discrétionnaire", "Consommation de base": "Conso. de base", "Santé": "Santé", "Services aux collectivités": "Services aux collectivités", "Energie": "Énergie", "Énergie": "Énergie", "Liquidités": "Liquidités", "Cash": "Liquidités"}
    secs = []
    for lab, canon in names.items():
        m = re.search(r"^" + re.escape(lab) + r"\s+(\d{1,2}[,.]\d)\s*%", txt, re.M | re.I)
        if m and canon not in [s[0] for s in secs]: secs.append([canon, _f(m.group(1).replace(".", ","))])
    if len(secs) >= 6 and 98 <= sum(x[1] for x in secs) <= 102: rep["sectors"] = sorted(secs, key=lambda x: -x[1])
    caps = []
    for lab, rx in (("< 150 M€", r"Moins de 150|<\s*150"), ("150 – 500 M€", r"150\s*M?€?\s*[-–à]\s*500"), ("500 M€ – 1 Md€", r"500\s*M?€?\s*[-–à]\s*1"), ("> 1 Md€", r"Plus de 1|>\s*1\s*(?:Md|bn)"), ("Liquidités", r"Liquidit|Cash")):
        m = re.search(r"(?:" + rx + r")[^\n\d]{0,25}(\d{1,2}[,.]\d)\s*%", txt, re.I)
        if m: caps.append([lab, _f(m.group(1).replace(".", ","))])
    if len(caps) >= 4 and 98 <= sum(x[1] for x in caps) <= 102: rep["capBuckets"] = caps
    out["report"] = rep
    return out

def hmg_perf():
    page = get(HMG_PAGE).decode("utf8", "ignore")
    nonce = re.search(r"nonce:\s*['\"]([0-9a-f]+)['\"]", page); fid = re.search(r"HMGChart\.init\(\s*['\"][^'\"]+['\"]\s*,\s*(\d+)", page)
    if not (nonce and fid): raise ValueError("graphique HMG introuvable")
    import urllib.parse, datetime as dt
    def call(period):
        body = urllib.parse.urlencode({"action": "hmg_fm_chart_data", "fond_id": fid.group(1), "period": period, "nonce": nonce.group(1)}).encode()
        req = urllib.request.Request(HMG_AJAX, data=body, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124 Safari/537.36", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                                                                  "Origin": "https://www.hmgfinance.com", "Referer": HMG_PAGE})
        j = json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf8"))
        if not j.get("success"): raise ValueError("réponse négative du site")
        d = j["data"]; L, f, b = d["labels"], d["fond"], d["indice"]
        if not (len(L) == len(f) == len(b) >= 100): raise ValueError("série trop courte")
        day = lambda s: round(dt.datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() / 86400)
        pts = [[day(l), round(x, 1), round(y, 1)] for l, x, y in zip(L, f, b)]
        if any(pts[i][0] >= pts[i + 1][0] for i in range(len(pts) - 1)): raise ValueError("dates non croissantes")
        if len(pts) > 600:
            st = len(pts) / 280; pts = [pts[int(k * st)] for k in range(280)] + [pts[-1]]
        yrs = max((pts[-1][0] - pts[0][0]) / 365.25, 0.1)
        fm = lambda v: ("%.1f" % v).replace(".", ",") + " %"
        mk = lambda v, annual: {"cum": fm(v - 100), "ann": fm(((v / 100) ** (1 / yrs) - 1) * 100 if annual else v - 100), "vl": "", "from": L[0][:10], "to": L[-1][:10]}
        return {"period": L[0][:10], "f": mk(pts[-1][1], yrs > 1.2), "b": mk(pts[-1][2], yrs > 1.2), "p": pts}
    out = {}
    for key, per in (("all", "all"), ("y1", "1year")):
        try: out[key] = call(per)
        except Exception as e: print("hmg perf", key, ":", e, file=sys.stderr)
    return {"id": "hmg", "perf": out} if out else None

# ---------------------------------------------------------------- Gay-Lussac Gestion (Microcaps Europe, part I)
GL_PAGE = "https://www.gaylussacgestion.com/gestion-de-fonds/microcaps-europe/"
GL_ISIN = "FR001400QDM4"

def _gl_page():
    return urllib.request.urlopen(urllib.request.Request(GL_PAGE, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124 Safari/537.36"}), timeout=60).read().decode("utf8", "ignore")

def gaylussac():
    """Dernier reporting mensuel PDF : KPI, 5 premières lignes, capitalisations, géographie, secteurs, performances."""
    import pdfplumber
    html = _gl_page()
    links = [u for u in re.findall(r'value=["\']([^"\']*reporting-financier-mensuel[^"\']*\.pdf)', html, re.I)]
    pref = [u for u in links if "QDM4" in u.upper()] or [u for u in links if "microcaps" in u.lower()] or links
    if not pref: return None
    key = lambda u: (lambda m: (int(m.group(2)), int(m.group(1))) if m else (0, 0))(re.search(r"(\d{2})[_-](\d{4})", u))
    url = max(pref, key=key)
    if url.startswith("/"): url = "https://www.gaylussacgestion.com" + url
    with pdfplumber.open(io.BytesIO(get(url.replace(" ", "%20")))) as pdf:
        pg = pdf.pages[0]; t = pg.extract_text() or ""; words = pg.extract_words()
    out = {"id": "gaylussac", "url": GL_PAGE}; rep = {}
    mois = {"janvier": "01", "février": "02", "mars": "03", "avril": "04", "mai": "05", "juin": "06", "juillet": "07", "août": "08", "septembre": "09", "octobre": "10", "novembre": "11", "décembre": "12"}
    m = re.search(r"\d{1,2}\s+([a-zéû]+)\s+(\d{4})", t, re.I)
    if m and m.group(1).lower() in mois:
        import calendar
        mo, yr = int(mois[m.group(1).lower()]), int(m.group(2))
        out["asOf"] = f"{calendar.monthrange(yr, mo)[1]:02d}/{mo:02d}/{yr}"; rep["asOf"] = m.group(1).lower() + " " + m.group(2)
    m = re.search(r"Actif net de la part\s+(\d[\d,]*)\s*M€", t)
    if m: out["aum"] = m.group(1).replace(".", ",") + " M€"
    m = re.search(r"Nombre de lignes\s+(\d{2,3})", t)
    if m: out["nbHoldings"] = int(m.group(1))
    top = re.findall(r"^([A-Z0-9&.'\- ]{3,40}?)\s+(\d{1,2},\d)%", t, re.M)
    bad = ("TAUX", "NOMBRE", "PER", "VE", "ROCE", "ROE", "BETA", "DETTE", "MARGE", "EV", "P/", "RENDEMENT", "ACTIF", "FRAIS", "VOLATIL", "SHARPE", "CAPITALISATION")
    hold = [{"name": re.sub(r"\s+(Sa|Spa|Plc|Ab|Asa|Oyj|Nv|Ag|Se)$", "", _fixname(n.strip().title())), "ticker": "", "pct": _f(p)} for n, p in top if not n.strip().startswith(bad)][:5]
    if len(hold) == 5 and all(h["pct"] <= 10 for h in hold) and 15 <= sum(h["pct"] for h in hold) <= 35: out["holdings"] = hold; out["holdingsLabel"] = "Cinq premières positions (reporting mensuel, part I)"
    # capitalisations
    cb = []
    for lab, rx in (("< 150 M€", r"Moins de 150 M€\s+(\d{1,2},\d)%"), ("150 – 500 M€", r"De 150 à 500 M€\s+(\d{1,2},\d)%"), ("> 500 M€", r"Plus de 500 M€\s+(\d{1,2},\d)%")):
        m = re.search(rx, t)
        if m: cb.append([lab, _f(m.group(1))])
    if len(cb) == 3 and 98 <= sum(x[1] for x in cb) <= 102: rep["capBuckets"] = cb
    m = re.search(r"Capitalisation moyenne \(M€\)\s+([\d\s]+,\d)", t); rep["capAvg"] = m.group(1).strip() + " M€" if m else None
    m = re.search(r"Capitalisation médiane \(M€\)\s+([\d\s]+,\d)", t); rep["capMed"] = m.group(1).strip() + " M€" if m else None
    rep = {k: v for k, v in rep.items() if v}
    # géographie (colonne gauche) et secteurs (colonne droite) : libellé + valeur sur la même ligne
    def column(lo, hi, vlo, vhi):
        res = []
        for w in words:
            if vlo <= w["x0"] <= vhi and re.fullmatch(r"\d{1,2},\d%", w["text"]):
                lab = " ".join(x["text"] for x in sorted(words, key=lambda x: x["x0"]) if lo <= x["x0"] < w["x0"] - 2 and abs(x["top"] - w["top"]) < 3 and not x["text"].endswith("%"))
                if lab: res.append([lab, _f(w["text"].rstrip("%")), w["top"]])
        res.sort(key=lambda r: r[2]); return [[a, b] for a, b, _ in res]
    anchor = [w for w in words if w["text"] == "Répartition"]
    if len(anchor) >= 2:
        xs = sorted(a["x0"] for a in anchor); split = (xs[0] + xs[-1]) / 2 if xs[-1] - xs[0] > 100 else 300
        geo = column(0, split, 0, split); sec = column(split, 9999, split, 9999)
        if 97 <= sum(x[1] for x in geo) <= 103 and len(geo) >= 5: rep["geo"] = geo
        if 97 <= sum(x[1] for x in sec) <= 103 and len(sec) >= 4: rep["sectors"] = sec
    # performances (Fonds / Indice : 1M, YTD, 2025…2021, Création)
    rows = re.findall(r"^(Fonds|Indice)\s+((?:-?\d+,\d%\s*){8})", t, re.M)
    if len(rows) >= 2:
        f, b = [x.replace("%", "") for x in rows[0][1].split()], [x.replace("%", "") for x in rows[1][1].split()]
        gap = lambda a, c: ("%+.1f" % (_f(a) - _f(c))).replace(".", ",").replace("+", "+") + " pt"
        fmt = lambda x: x + " %"
        lab = ["1 mois", "Depuis le 1er janv.", "2025", "2024", "2023", "2022", "2021", "Depuis la création"]
        cum = [0, 1, 7]; cal = [2, 3, 4, 5, 6]
        mk = lambda ix: [[lab[i], fmt(f[i]), fmt(b[i]), gap(f[i], b[i])] for i in ix]
        rep["tables"] = [{"title": "Performances cumulées", "cols": ["Fonds", "Indice", "Écart"], "rows": mk(cum), "note": "Indice : MSCI Europe Micro Caps NR. Objectif de gestion : 7 % annualisés."},
                         {"title": "Performances calendaires", "cols": ["Fonds", "Indice", "Écart"], "rows": mk(cal[::-1])}]
    if rep: out["report"] = rep
    return out if len(out) > 2 else None

def gaylussac_perf():
    """Historique de VL de la part I (graphique amCharts de la page) vs objectif de gestion de 7 % annualisés."""
    import datetime as dt
    html = _gl_page()
    scripts = [s for s in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S) if GL_ISIN in s and "var data" in s]
    if not scripts: raise ValueError("graphique Gay-Lussac introuvable")
    blk = scripts[0].split("var data", 1)[1]
    pts = re.findall(r"""["']?date["']?\s*:\s*["']([^"']+)["']\s*,\s*["']?value["']?\s*:\s*["']?(-?[\d.]+)""", blk)
    if len(pts) < 100: raise ValueError("série trop courte")
    def day(s):
        s = s[:10]
        try: return round(dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() / 86400)
        except ValueError: return round(dt.datetime.strptime(s, "%d/%m/%Y").replace(tzinfo=dt.timezone.utc).timestamp() / 86400)
    ser = [(day(d), float(v)) for d, v in pts]
    clean = []
    for d, v in ser:
        if v <= 0: continue
        if clean and d <= clean[-1][0]: break          # une 2e série ou une ligne parasite : on s'arrête
        clean.append((d, v))
    ser = clean
    if len(ser) < 100: raise ValueError("série trop courte après nettoyage")
    fm = lambda v: ("%.1f" % v).replace(".", ",") + " %"
    def build(ser, annual):
        d0, v0 = ser[0]; step = max(1, len(ser) // 280)
        sel = ser[::step] + ([ser[-1]] if (len(ser) - 1) % step else [])
        P = [[d, round(100 * v / v0, 2), round(100 * 1.07 ** ((d - d0) / 365.25), 2)] for d, v in sel]
        yrs = max((ser[-1][0] - d0) / 365.25, 0.1)
        cum = ser[-1][1] / v0 * 100 - 100; ann = ((1 + cum / 100) ** (1 / yrs) - 1) * 100 if annual else cum
        bc = P[-1][2] - 100; ba = 7.0 if annual else bc
        iso = lambda d: dt.datetime.fromtimestamp(d * 86400, dt.timezone.utc).strftime("%Y-%m-%d")
        return {"period": dt.datetime.fromtimestamp(d0 * 86400, dt.timezone.utc).strftime("%d/%m/%Y"),
                "f": {"cum": fm(cum), "ann": fm(ann), "vl": ("%.2f" % ser[-1][1]).replace(".", ","), "from": iso(d0), "to": iso(ser[-1][0])},
                "b": {"cum": fm(bc), "ann": fm(ba), "vl": "", "from": iso(d0), "to": iso(ser[-1][0])}, "p": P}
    full = build(ser, True)
    last = ser[-1][0]; y1s = [x for x in ser if x[0] >= last - 365]
    out = {"all": full}
    if len(y1s) >= 50: out["y1"] = build(y1s, False)
    ytd0 = [x for x in ser if x[0] < round(dt.datetime(dt.datetime.fromtimestamp(last * 86400, dt.timezone.utc).year, 1, 1, tzinfo=dt.timezone.utc).timestamp() / 86400)]
    if ytd0: out["all"]["f"]["ytd"] = fm(ser[-1][1] / ytd0[-1][1] * 100 - 100)
    return {"id": "gaylussac", "perf": out}


# ---------------------------------------------------------------- Amiral Gestion (Sextant PME, part A)
SX_PAGE = "https://www.amiralgestion.com/fr/sextant-pme"
SX_ISIN = "FR0010547869"
SX_API = "https://amiral.dev.calcifer.nx.digital/api/data/public/"   # source utilisée par le site lui-même (courbe de VL)

def _sx_page():
    return urllib.request.urlopen(urllib.request.Request(SX_PAGE, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124 Safari/537.36"}), timeout=60).read().decode("utf8", "ignore")

def sextant():
    """Reporting mensuel PDF de Sextant PME (part A) : KPI, 5 premières lignes, allocations, performances, contributeurs."""
    import pdfplumber
    html = _sx_page().replace("\\/", "/")
    m = re.search(r'https://api\.amiralgestion\.com/documents/permalink/\d+/[^"\'<>\s]*?\.pdf[^"\'<>\s]*', html)
    links = re.findall(r'href=["\'](https://api\.amiralgestion\.com/documents/permalink/\d+/doc\.pdf)["\'][^>]*>\s*(?:<[^>]+>\s*)*Sextant PME - A - Mensuel', html, re.I)
    if not links: raise ValueError("lien du reporting mensuel introuvable")
    with pdfplumber.open(io.BytesIO(get(links[0]))) as pdf:
        p1, p2 = pdf.pages[0], pdf.pages[1]
        t1, t2 = p1.extract_text() or "", p2.extract_text() or ""
        w2 = p2.extract_words()
    out = {"id": "amiral", "url": SX_PAGE}; rep = {}
    num = lambda s: _f(s.replace(" ", ""))
    m = re.search(r"Actif net\s+([\d.]+)\s*M€", t1)
    if m: out["aum"] = m.group(1).replace(".", ",") + " M€"
    m = re.search(r"Nombre de lignes\s+(\d{2,3})", t1)
    if m: out["nbHoldings"] = int(m.group(1))
    m = re.search(r"Rapport Mensuel\s*/\s*([A-Za-zéûÉ]+)\s+(\d{4})", t1 + "\n" + t2)
    mois = {"janvier": "01", "février": "02", "mars": "03", "avril": "04", "mai": "05", "juin": "06", "juillet": "07", "août": "08", "septembre": "09", "octobre": "10", "novembre": "11", "décembre": "12"}
    if m and m.group(1).lower() in mois:
        import calendar
        mo, yr = int(mois[m.group(1).lower()]), int(m.group(2))
        out["asOf"] = f"{calendar.monthrange(yr, mo)[1]:02d}/{mo:02d}/{yr}"; rep["asOf"] = m.group(1).lower() + " " + m.group(2)
    m = re.search(r"Capitalisation moyenne des actions\s+([\d.]+)\s*Md€", t1); rep["capAvg"] = m.group(1).replace(".", ",") + " Md€" if m else None
    m = re.search(r"Capitalisation médiane des actions\s+([\d.]+)\s*Md€", t1); rep["capMed"] = m.group(1).replace(".", ",") + " Md€" if m else None
    # cinq premières lignes : « Nom Actions Secteur Pays Note %»
    rows = re.findall(r"^(.+?)\s+Actions\s+.+?\s+\d\.\d\s+(\d{1,2}\.\d)%\s*$", t2, re.M)
    hold = [{"name": n.strip(), "ticker": "", "pct": num(p)} for n, p in rows][:5]
    if len(hold) == 5: out["holdings"] = hold
    for lab, key in (("10 premières", "top10"),):
        mm = re.search(lab + r"\s+lignes\s+(\d{2}\.\d)%", t2)
        if mm: out[key] = num(mm.group(1))
    # allocations : mots positionnés (colonnes : classes d'actifs x<200, secteurs 200-410, pays >410)
    def column(x0, x1, vx0, vx1):
        res = []
        for w in w2:
            if vx0 <= w["x0"] <= vx1 and re.fullmatch(r"\d{1,2}\.\d%", w["text"]):
                lab = " ".join(x["text"] for x in sorted(w2, key=lambda x: (round(x["top"]), x["x0"])) if x0 <= x["x0"] < w["x0"] - 2 and -4 < w["top"] - x["top"] < 22 and not re.search(r"%|bps", x["text"]))
                if lab: res.append([lab, num(w["text"].rstrip("%")), w["top"]])
        res.sort(key=lambda r: r[2]); return [[a, b] for a, b, _ in res]
    sec = column(200, 410, 200, 410); geo = column(410, 9999, 410, 9999)
    if 97 <= sum(x[1] for x in sec) <= 103 and len(sec) >= 5: rep["sectors"] = sec
    if 97 <= sum(x[1] for x in geo) <= 103 and len(geo) >= 5: rep["geo"] = geo
    mc = {"Jusqu'à 500 M€": r"<=500M€\s+(\d{1,2}\.\d)%", "500 M€ – 2 Md€": r"500M€ - 2Md€\s+(\d{1,2}\.\d)%", "> 2 Md€": r">2Md€\s+(\d{1,2}\.\d)%", "Liquidités": r"Liquidités\s+(\d{1,2}\.\d)%"}
    cb = [[k, num(re.search(v, t2).group(1))] for k, v in mc.items() if re.search(v, t2)]
    if len(cb) >= 3 and 97 <= sum(x[1] for x in cb) <= 103: rep["capBuckets"] = cb
    # performances cumulées
    lab = ["1 mois", "Depuis le 1er janv.", "1 an", "3 ans", "5 ans", "10 ans", "Depuis la création"]
    pr = re.findall(r"^(Fonds|Indice de référence)\s+((?:-?\d+\.\d%\s*){7,11})", t1, re.M)
    if len(pr) >= 2:
        f = [x.rstrip("%") for x in pr[0][1].split()]; b = [x.rstrip("%") for x in pr[1][1].split()]
        if len(f) >= 7 and len(b) >= 7:
            fm = lambda x: x.replace(".", ",") + " %"
            gap = lambda a, c: ("%+.1f" % (num(a) - num(c))).replace(".", ",") + " pt" + ("s" if abs(num(a) - num(c)) >= 2 else "")
            rep["tables"] = [{"title": "Performances cumulées", "cols": ["Fonds", "MSCI EMU Micro NR", "Écart"], "rows": [[lab[i], fm(f[i]), fm(b[i]), gap(f[i], b[i])] for i in range(7)],
                              "note": "Le 1er juin 2021, l'indice de référence a été modifié (auparavant CAC Small NR)."}]
    # contributeurs : « Optima Bank 29bps »
    best = re.findall(r"^(.+?)\s+(\d{2,3})bps\s+(.+?)\s+(-\d{2,3})bps\s*$", t1, re.M)
    if len(best) >= 5:
        rep["moves"] = {"title": "Contributeurs du mois", "best": [[a.strip(), round(int(b) / 100, 2)] for a, b, _, _ in best[:5]], "worst": [[c.strip(), round(int(d) / 100, 2)] for _, _, c, d in best[:5]]}
    rep = {k: v for k, v in rep.items() if v}
    if rep: out["report"] = rep
    return out if len(out) > 2 else None

def sextant_perf():
    """Historique de VL de la part A (CSV du site) + performances comparées à l'indice (perf.json du site)."""
    import datetime as dt, csv
    q = f"fund_id=sextant_pme&share_id={SX_ISIN}"
    rows = list(csv.reader(io.StringIO(get(SX_API + "histo.csv?" + q).decode("utf8"))))[1:]
    day = lambda s: round(dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() / 86400)
    ser = [(day(r[0]), float(r[3])) for r in rows if len(r) >= 4 and r[3]]
    if len(ser) < 200: raise ValueError("historique de VL trop court")
    pj = json.loads(get(SX_API + "perf.json?" + q).decode("utf8"))
    P = {p["period"]: p for p in pj.get("performances", [])}
    fm = lambda v: ("%.1f" % (v * 100)).replace(".", ",") + " %"
    iso = lambda d: dt.datetime.fromtimestamp(d * 86400, dt.timezone.utc).strftime("%Y-%m-%d")
    def build(ser, key):
        step = max(1, len(ser) // 300); sel = ser[::step] + ([ser[-1]] if (len(ser) - 1) % step else [])
        v0 = ser[0][1]; p = P.get(key, {})
        g = lambda k: fm(p[k]) if p.get(k) is not None else ""
        blk = lambda a, b: {"cum": g(a), "ann": g(b), "vl": "", "from": iso(ser[0][0]), "to": iso(ser[-1][0])}
        f = blk("fund", "annualized_fund"); f["vl"] = ("%.2f" % ser[-1][1]).replace(".", ",")
        return {"period": dt.datetime.fromtimestamp(ser[0][0] * 86400, dt.timezone.utc).strftime("%d/%m/%Y"), "f": f, "b": blk("benchmark", "annualized_benchmark"), "p": [[d, round(100 * v / v0, 2)] for d, v in sel]}
    full = build(ser, "Inception"); last = ser[-1][0]
    out = {"all": full}
    y1 = [x for x in ser if x[0] >= last - 365]
    if len(y1) >= 50: out["y1"] = build(y1, "1Y")
    if "YTD" in P:
        full["f"]["ytd"] = fm(P["YTD"]["fund"]); full["b"]["ytd"] = fm(P["YTD"]["benchmark"])
    # indice : courbe non publiée -> points déduits des performances officielles (origine, 1 an, YTD), tracés en pointillés
    try:
        bi, b1, by = (P[k]["benchmark"] for k in ("Inception", "1Y", "YTD"))
        E = 100 * (1 + bi); yd = (y1[0][0] if len(y1) >= 50 else last - 365)
        d31 = round(dt.datetime(dt.datetime.fromtimestamp(last * 86400, dt.timezone.utc).year - 1, 12, 31, tzinfo=dt.timezone.utc).timestamp() / 86400)
        vy, vt = E / (1 + b1), E / (1 + by)
        if ser[0][0] < yd < d31 < last:
            full["bsp"] = [[ser[0][0], 100.0], [yd, round(vy, 2)], [d31, round(vt, 2)], [last, round(E, 2)]]
            if "y1" in out: out["y1"]["bsp"] = [[yd, 100.0], [d31, round(100 * vt / vy, 2)], [last, round(100 * E / vy, 2)]]
    except (KeyError, TypeError, ZeroDivisionError):
        pass
    return {"id": "amiral", "perf": out}


# ---------------------------------------------------------------- Fundsmith Equity Fund (SICAV, classe T Acc EUR)
FS_PAGE = "https://www.fundsmith.eu/fr/fiche-d-information"

def _txt(html):
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)</t[dh]>", "\t", html); html = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h\d|section|table)>", "\n", html)
    import html as H
    t = H.unescape(re.sub(r"<[^>]+>", " ", html)).replace("\xa0", " ")
    return "\n".join(re.sub(r"[ ]+", " ", l).strip() for l in t.splitlines() if l.strip())

def _fs_text():
    return _txt(get(FS_PAGE).decode("utf8", "ignore"))

def _num(x): return float(x.replace(",", ".").replace("+", ""))

def fundsmith():
    """Fiche d'information officielle de la SICAV : chiffres clés, top 10 (noms), pays, secteurs, commentaire."""
    t = _fs_text(); out = {"id": "fundsmith", "url": FS_PAGE}; rep = {}
    mois = {"janv": "01", "févr": "02", "mars": "03", "avr": "04", "mai": "05", "juin": "06", "juil": "07", "août": "08", "sept": "09", "oct": "10", "nov": "11", "déc": "12"}
    m = re.search(r"En date du (\d{1,2}) ([a-zéû]+)\.? (\d{4})", t, re.I)
    if m and m.group(2).lower()[:4].rstrip(".") in [k[:4] for k in mois]:
        k = next(k for k in mois if m.group(2).lower().startswith(k)); out["asOf"] = f"{int(m.group(1)):02d}/{mois[k]}/{m.group(3)}"
        full = {"01": "janvier", "02": "février", "03": "mars", "04": "avril", "05": "mai", "06": "juin", "07": "juillet", "08": "août", "09": "septembre", "10": "octobre", "11": "novembre", "12": "décembre"}[mois[k]]
        rep["asOf"] = f"{int(m.group(1))} {full} {m.group(3)}"
    m = re.search(r"Taille du fonds\s*€\s*([\d.,]+)\s*bn", t)
    if m: out["aum"] = m.group(1).replace(".", ",") + " Md€"
    m = re.search(r"Nombre de titres\s*(\d+)", t)
    if m: out["nbHoldings"] = int(m.group(1))
    m = re.search(r"Capitalisation boursière médiane\s*€\s*([\d.,]+)\s*bn", t)
    if m: rep["capMed"] = m.group(1).replace(".", ",") + " Md€"
    def block(a, b):
        mm = re.search(a + r"(.*?)" + b, t, re.S)
        return mm.group(1) if mm else ""
    def pairs(txt, tr):
        res = []
        for lab, v in re.findall(r"^(.+?)[\s\t]+(\d{1,2}\.\d)\s*$", txt, re.M):
            res.append([tr.get(lab.strip(), lab.strip()), _num(v)])
        return res
    geo = pairs(block(r"Répartition géographique", r"Répartition sectorielle"), {"US": "États-Unis", "UK": "Royaume-Uni"})
    sec = pairs(block(r"Répartition sectorielle", r"Codes d.identification"), {"Technologies de l’information": "Technologies de l'information", "Biens de consommation discrétionnaire": "Biens de conso. discrétionnaire", "Produits de consommation courante": "Produits de consommation courante"})
    if len(geo) >= 3 and 97 <= sum(x[1] for x in geo) <= 103: rep["geo"] = geo
    if len(sec) >= 5 and 97 <= sum(x[1] for x in sec) <= 103: rep["sectors"] = sec
    m = re.search(r"Commentaire sur le portefeuille[^\n]*\n(.+?)\nLes 10 participations", t, re.S)
    if m: rep["comment"] = " ".join(m.group(1).split())
    top = [l.strip() for l in block(r"Les 10 participations principales", r"Our values").splitlines() if l.strip()][:10]
    if len(top) == 10 and all(len(x) < 40 for x in top):
        out["holdings"] = [{"name": n, "ticker": "", "pct": None} for n in top]
        out["holdingsLabel"] = "Les 10 participations principales (fiche de " + (rep.get("asOf") or "") + " — noms publiés, poids non communiqués)"
    if rep: out["report"] = rep
    return out if len(out) > 2 else None

def iwda_monthly():
    """Clôtures mensuelles de l'ETF iShares Core MSCI World (IWDA.AS, EUR, capitalisation) : proxy de l'indice MSCI World net."""
    req = urllib.request.Request("https://query1.finance.yahoo.com/v8/finance/chart/IWDA.AS?range=max&interval=1mo", headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
    d = json.loads(urllib.request.urlopen(req, timeout=60).read())["chart"]["result"][0]
    out = {}
    for t, c in zip(d["timestamp"], d["indicators"]["quote"][0]["close"]):
        if c is None: continue
        x = datetime.datetime.fromtimestamp(t + 7200, datetime.timezone.utc); out[(x.year, x.month)] = float(c)
    if len(out) < 100: raise ValueError("série IWDA trop courte")
    return out

def fundsmith_perf():
    """Courbe base 100 chaînée à partir du tableau des rendements mensuels (classe T Acc, EUR) + statistiques de la fiche."""
    import datetime as dt, calendar
    t = _fs_text(); rows = {}
    for y, rest in re.findall(r"^(20\d\d)\s+(.+)$", block_perf(t), re.M):
        vals = re.findall(r"[+-]?\d+\.\d", rest)
        if len(vals) < 2: continue
        rows[int(y)] = [float(v) for v in vals[:-1]]          # le dernier chiffre = total de l'année
    if len(rows) < 10: raise ValueError("tableau mensuel Fundsmith introuvable")
    y0 = min(rows); pts = []; v = 100.0
    day = lambda d: (d - dt.date(1970, 1, 1)).days
    for y in sorted(rows):
        r = rows[y]; first = 12 - len(r) if y == y0 else 0
        for i, x in enumerate(r):
            m = first + i + 1
            if not pts: pts.append([day(dt.date(y, m, 1)) - 1, 100.0])
            v *= 1 + x / 100; pts.append([day(dt.date(y, m, calendar.monthrange(y, m)[1])), round(v, 2)])
    fm = lambda a: ("%.1f" % a).replace(".", ",") + " %"
    last = pts[-1]; iso = lambda d: (dt.date(1970, 1, 1) + dt.timedelta(days=d)).isoformat()
    yrs = (last[0] - pts[0][0]) / 365.25
    ann = (last[1] / 100) ** (1 / yrs) * 100 - 100
    p12 = pts[-13:]; y1 = [[d, round(100 * x / p12[0][1], 2)] for d, x in p12]
    mk = lambda c, a, p, vl="": {"cum": fm(c), "ann": fm(a), "vl": vl, "from": iso(p[0][0]), "to": iso(p[-1][0])}
    # indice de référence : proxy ETF (3e colonne). En cas d'échec, la tâche échoue et la page garde ses données intégrées.
    mw = iwda_monthly()
    mo = lambda d: ((dt.date(1970, 1, 1) + dt.timedelta(days=d)).year, (dt.date(1970, 1, 1) + dt.timedelta(days=d)).month)
    def with_b(pp):
        base = mw[mo(pp[0][0])]
        return [[d, v, round(100 * mw[mo(d)] / base, 2)] for d, v in pp]
    pts = with_b(pts); y1 = with_b(y1); p12 = y1
    allp = {"period": dt.date.fromordinal(dt.date(1970, 1, 1).toordinal() + pts[0][0] + 1).strftime("%d/%m/%Y"), "f": mk(last[1] - 100, ann, pts), "b": mk(0, 0, pts), "p": pts}
    rb = pts[-1][2] / 100; ab = rb ** (1 / yrs)
    allp["b"] = {"cum": fm((rb - 1) * 100), "ann": fm((ab - 1) * 100), "vl": "", "from": iso(pts[0][0]), "to": iso(last[0])}
    allp["f"]["cum"] = fm(pts[-1][1] - 100)
    mv = re.search(r"T Class Acc\s+[\d.]+%\s+EUR [\d\s]+\s+([\d.]+)\s*€", t)
    if mv: allp["f"]["vl"] = mv.group(1).replace(".", ",")
    ytd = re.search(r"Fundsmith\d?\s+[+-]?\d+\.\d\s+([+-]?\d+\.\d)", t)
    if ytd: allp["f"]["ytd"] = fm(_num(ytd.group(1)))
    y1blk = {"period": dt.date.fromordinal(dt.date(1970, 1, 1).toordinal() + p12[0][0]).strftime("%d/%m/%Y"), "f": mk(p12[-1][1] / p12[0][1] * 100 - 100, p12[-1][1] / p12[0][1] * 100 - 100, p12), "b": {"cum": fm(p12[-1][2] - 100), "ann": fm(p12[-1][2] - 100), "vl": "", "from": iso(p12[0][0]), "to": iso(p12[-1][0])}, "p": y1}
    return {"id": "fundsmith", "perf": {"all": allp, "y1": y1blk}}

def block_perf(t):
    m = re.search(r"Performance, % rendement total(.*?)Commentaire sur le portefeuille", t, re.S)
    m2 = re.search(r"Tableau de performance mensuelle(.*?)Dividendes par action", t, re.S)
    return (m.group(1) if m else "") + "\n" + (m2.group(1) if m2 else "")

# ---------------------------------------------------------------- Comgest Growth Europe Opportunities (EUR Acc)
CG_PAGE = "https://www.comgest.com/fr/fr/particulier/fonds/comgest-growth-europe-opportunities-eur-acc"

def comgest():
    """Chiffres clés de la page fonds (VL, actif total, nombre de titres, poids du top 10)."""
    t = _txt(get(CG_PAGE).decode("utf8", "ignore")); out = {"id": "comgest", "url": CG_PAGE}
    m = re.search(r"Actif total du fonds, en millions\s+([\d.,]+)\s*EUR", t)
    if m: out["aum"] = m.group(1).replace(".", ",") + " M€"
    m = re.search(r"Nombre de titres\s+(\d+)", t)
    if m: out["nbHoldings"] = int(m.group(1))
    m = re.search(r"Poids des 10 principales positions\s+([\d.,]+)\s*%", t)
    if m: out["top10"] = _f(m.group(1))
    vl = re.search(r"Valeur liquidative\s+([\d.,]+)\s*EUR", t); dv = re.search(r"Date de la valeur liquidative\s+(\d\d/\d\d/\d{4})", t)
    if vl and dv:
        out["extra"] = [["Valeur liquidative (" + dv.group(1) + ")", vl.group(1).replace(".", ",") + " €"], ["Indice de comparaison", "MSCI Europe NR"], ["Active share", "83,7 %"], ["Frais courants", "1,60 %"],
                        ["Frais de gestion", "1,50 %"], ["ISIN", "IE00B4ZJ4188"], ["SFDR", "Article 8"], ["Éligibilité PEA", "Non"], ["Création du fonds", "07/08/2009"]]
    return out if len(out) > 2 else None


def main():
    funds = []
    for job in [fundsmith, fundsmith_perf, comgest] + [(lambda c=c: independance(c)) for c in IAM_FUNDS] + [(lambda c=c: independance_perf(c)) for c in IAM_FUNDS] + [sunny, sunny_perf, hmg, hmg_perf, gaylussac, gaylussac_perf, sextant, sextant_perf] + [lambda f=f, u=u: factsheet(f, u) for f, u in FACTSHEETS.items() if u]:
        try:
            r = job()
            if r: funds.append(r)
        except Exception as e:
            print("ignoré :", e, file=sys.stderr)
    out = {"generatedAt": datetime.datetime.utcnow().isoformat() + "Z", "funds": funds}
    json.dump(out, open("data/funds.json", "w", encoding="utf8"), ensure_ascii=False, indent=1)
    print(len(funds), "fonds mis à jour")

main()
