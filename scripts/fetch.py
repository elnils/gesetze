#!/usr/bin/env python3
"""
Gesetze-Scraper für gesetze-im-internet.de
- index.json: nur Bezeichnung + Titel + Gesetz (klein, schnell ladbar)
- data/{gesetz}/data.json: Volltext (wird on-demand geladen)
"""

import json, re, time, argparse, zipfile, io
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
import urllib.request, urllib.error

BASE_URL = "https://www.gesetze-im-internet.de"
TOC_URL  = f"{BASE_URL}/gii-toc.xml"
DATA_DIR = Path(__file__).parent.parent / "data"
HEADERS  = {"User-Agent": "gesetze-de-repo/2.0 (github.com; open-source)"}

STARTER = [
    "GG","BGB","StGB","HGB","ZPO","StPO","UrhG","UWG","GWB",
    "TMG","TTDSG","BDSG_2018","EstG","InsO","KSchG","ArbGG",
    "BetrVG","AO_1977","UStG_1980","PatG","MarkG","VwGO","VwVfG",
    "AufenthG_2004","AsylG","StVZO","StVO","SGB_1","SGB_5","GwG_2017",
]
TEST_SET = ["GG", "BGB", "StGB"]

def fetch(url):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
            print(f"  HTTP {e.code} – Versuch {attempt+1}/3")
        except Exception as e:
            print(f"  Fehler: {e}")
        if attempt < 2: time.sleep(1.5 * (attempt+1))
    return None

def load_toc():
    print("→ Lade gii-toc.xml …")
    data = fetch(TOC_URL)
    if not data:
        print("  ✗ Nicht erreichbar"); return {}
    root = ET.fromstring(data)
    toc = {}
    for item in root.iter("item"):
        title = (item.findtext("title") or item.get("title","")).strip()
        link  = (item.findtext("link")  or item.get("link", item.get("url",""))).strip()
        if title and link:
            toc[title.upper()] = link
    if not toc:
        for el in root.iter():
            t = (el.text or "").strip()
            if "xml.zip" in t:
                m = re.search(r'/([a-z0-9_]+)/xml\.zip', t, re.I)
                if m:
                    k = m.group(1).upper()
                    toc[k] = t if t.startswith("http") else f"{BASE_URL}{t}"
    print(f"  ✓ {len(toc)} Einträge gefunden")
    return toc

def parse_xml(data, kuerzel):
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml_files = [f for f in z.namelist() if f.endswith(".xml")]
                if not xml_files: return {}
                xml_file = max(xml_files, key=lambda f: z.getinfo(f).file_size)
                data = z.read(xml_file)
        except: pass
    try: root = ET.fromstring(data)
    except ET.ParseError as e:
        print(f"  ✗ XML-Fehler: {e}"); return {}
    tag = root.tag
    ns = tag.split("}")[0]+"}" if "{" in tag else ""
    def t(n): return f"{ns}{n}"
    paragraphen, meta = [], {}
    for norm in root.findall(f".//{t('norm')}"):
        md = norm.find(t("metadaten"))
        if md is None: continue
        enbez = md.findtext(t("enbez"),"").strip()
        titel = md.findtext(t("titel"),"").strip()
        if not enbez and not meta:
            meta = {
                "langtitel": md.findtext(t("langue"),""),
                "kurztitel": md.findtext(t("jurabk"), kuerzel),
                "ausfertigungsdatum": md.findtext(t("ausfertigung-datum"),""),
            }
            continue
        if not enbez: continue
        text_el = norm.find(f".//{t('textdaten')}")
        inhalt = ""
        if text_el is not None:
            inhalt = re.sub(r"\s+", " ",
                ET.tostring(text_el, encoding="unicode", method="text")).strip()
        paragraphen.append({
            "bezeichnung": enbez,
            "titel":       titel,
            "inhalt":      inhalt[:3000],
        })
    return {"meta": meta, "paragraphen": paragraphen, "count": len(paragraphen)}

def fetch_gesetz(kuerzel, xml_url, name=""):
    kl = kuerzel.lower()
    print(f"  → {kuerzel}", end=" ")
    data = fetch(xml_url) or fetch(f"{BASE_URL}/{kl}/xml.zip")
    if not data:
        print("✗ nicht erreichbar"); return False
    parsed = parse_xml(data, kuerzel)
    if not parsed.get("paragraphen"):
        print("✗ keine Paragraphen"); return False
    out = DATA_DIR / kl
    out.mkdir(parents=True, exist_ok=True)
    result = {
        "kuerzel":   kl,
        "name":      name or parsed["meta"].get("langtitel", kuerzel),
        "quelle":    f"{BASE_URL}/{kl}/",
        "abgerufen": datetime.now(timezone.utc).isoformat(),
        **parsed,
    }
    with open(out/"data.json","w",encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"✓ {parsed['count']} §§")
    return True

def build_index():
    """
    Baut ZWEI Dateien:
    - index.json: nur Bezeichnung + Titel + Gesetz (klein, ~200KB)
    - search.json: zusätzlich 150 Zeichen Inhalt für KI-Kontext (~1MB)
    """
    print("\n→ Baue Index …")
    index = []   # klein – für Suche
    search = []  # mittel – für KI-Kontext

    for gdir in sorted(DATA_DIR.iterdir()):
        df = gdir/"data.json"
        if not df.exists(): continue
        with open(df,encoding="utf-8") as f: g = json.load(f)
        gname = g.get("name", g["kuerzel"].upper())
        for p in g.get("paragraphen",[]):
            index.append({
                "g": g["kuerzel"],           # gesetz (kurz)
                "n": gname,                  # name
                "b": p["bezeichnung"],       # bezeichnung
                "t": p["titel"],             # titel
            })
            search.append({
                "g": g["kuerzel"],
                "n": gname,
                "b": p["bezeichnung"],
                "t": p["titel"],
                "i": (p["inhalt"] or "")[:150],  # inhalt gekürzt
            })

    # index.json – minimal, für Fuse.js Suche
    with open(DATA_DIR/"index.json","w",encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",",":"))

    # search.json – mit Inhalt, für KI-Kontext
    with open(DATA_DIR/"search.json","w",encoding="utf-8") as f:
        json.dump(search, f, ensure_ascii=False, separators=(",",":"))

    meta = {
        "generiert": datetime.now(timezone.utc).isoformat(),
        "anzahl_eintraege": len(index),
        "gesetze": sorted({e["g"] for e in index}),
    }
    with open(DATA_DIR/"meta.json","w",encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # Größen ausgeben
    idx_size  = (DATA_DIR/"index.json").stat().st_size / 1024
    srch_size = (DATA_DIR/"search.json").stat().st_size / 1024
    print(f"  ✓ index.json:  {idx_size:.0f} KB ({len(index)} Einträge)")
    print(f"  ✓ search.json: {srch_size:.0f} KB (mit Inhalt)")
    print(f"  ✓ {len(meta['gesetze'])} Gesetze")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gesetz")
    parser.add_argument("--all",    action="store_true")
    parser.add_argument("--test",   action="store_true", help="Nur GG+BGB+StGB")
    parser.add_argument("--no-index", action="store_true")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    toc = load_toc()

    if args.test:
        auswahl = TEST_SET
        print(f"\n[Test: {len(auswahl)} Gesetze]\n")
    elif args.gesetz:
        auswahl = [args.gesetz.upper()]
    elif args.all:
        auswahl = list(toc.keys())
        print(f"\n[Alle: {len(auswahl)} Gesetze]\n")
    else:
        auswahl = STARTER
        print(f"\n[Starter: {len(auswahl)} Gesetze]\n")

    ok = 0
    for i, k in enumerate(auswahl, 1):
        print(f"[{i}/{len(auswahl)}]", end=" ")
        xml_url = toc.get(k.upper(), f"{BASE_URL}/{k.lower()}/xml.zip")
        if fetch_gesetz(k, xml_url):
            ok += 1
        time.sleep(0.3)

    print(f"\n✓ {ok}/{len(auswahl)} erfolgreich")
    if not args.no_index:
        build_index()

if __name__ == "__main__":
    main()
