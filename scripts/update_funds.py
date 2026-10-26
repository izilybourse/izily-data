#!/usr/bin/env python3
"""Met à jour data/funds.json pour la page « Supers investisseurs, politiciens & gérants de fonds ».
NON TESTÉ en conditions réelles : à lancer une première fois à la main (python scripts/update_funds.py) et à vérifier.

1) Fundsmith : dernier 13F-HR via SEC EDGAR (renseigner le CIK ci-dessous).
2) Autres sociétés : factsheet PDF mensuelle -> top 10 (regex "Nom ... 4,52 %"). Renseigner les URL dans FACTSHEETS.
   Les fiches dont l'URL est vide ou dont l'analyse échoue sont simplement ignorées : la page garde ses données intégrées.
"""
import io, json, re, sys, urllib.request, datetime, xml.etree.ElementTree as ET

UA = {"User-Agent": "izilyfire-data contact@izilyfire.fr"}  # la SEC exige un User-Agent identifiable
FUNDSMITH_CIK = ""  # ex. "0001234567" : à chercher sur sec.gov/cgi-bin/browse-edgar?company=fundsmith
FACTSHEETS = {      # id (identique à la page) -> URL du PDF mensuel le plus récent
    "comgest": "", "carmignac": "", "flossbach": "", "moneta": "", "independance-am": "",
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

def main():
    funds = []
    for job in [fundsmith_13f] + [lambda f=f, u=u: factsheet(f, u) for f, u in FACTSHEETS.items() if u]:
        try:
            r = job()
            if r: funds.append(r)
        except Exception as e:
            print("ignoré :", e, file=sys.stderr)
    out = {"generatedAt": datetime.datetime.utcnow().isoformat() + "Z", "funds": funds}
    json.dump(out, open("data/funds.json", "w", encoding="utf8"), ensure_ascii=False, indent=1)
    print(len(funds), "fonds mis à jour")

main()
