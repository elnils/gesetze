#!/usr/bin/env python3
"""
Gesetze-Scraper → Cloudflare D1
Lädt Gesetze von gesetze-im-internet.de und schreibt sie in D1.

Umgebungsvariablen (GitHub Secrets):
  CLOUDFLARE_API_TOKEN
  CLOUDFLARE_ACCOUNT_ID
  CLOUDFLARE_D1_DATABASE_ID
"""

import json, os, re, time, zipfile, io, sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
import urllib.request, urllib.error

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL = "https://www.gesetze-im-internet.de"
TOC_URL  = f"{BASE_URL}/gii-toc.xml"
HEADERS  = {"User-Agent": "gesetze-de-repo/3.0 (github.com; open-source)"}

CF_TOKEN   = os.environ["CLOUDFLARE_API_TOKEN"]
CF_ACCOUNT = os.environ["CLOUDFLARE_ACCOUNT_ID"]
CF_DB_ID   = os.environ["CLOUDFLARE_D1_DATABASE_ID"]
D1_URL     = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/d1/database/{CF_DB_ID}/query"

STARTER = [
    "GG","BGB","StGB","HGB","ZPO","StPO","UrhG","UWG","GWB",
    "TMG","TTDSG","BDSG_2018","EstG","InsO","KSchG","ArbGG",
    "BetrVG","AO_1977","UStG_1980","PatG","MarkG","VwGO","VwVfG",
    "AufenthG_2004","AsylG","StVZO","StVO","SGB_1","SGB_5","GwG_2017",
]
TEST_SET = ["GG", "BGB", "StGB"]

# ── HTTP ──────────────────────────────────────────────────────────────────────
def fetch(url, headers=None, retries=3):
    h = {**HEADERS, **(headers or {})}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
            print(f"  HTTP {e.code} – Versuch {attempt+1}/{retries}")
        except Exception as e:
            print(f"  Fehler: {e}")
        if attempt < retries-1: time.sleep(1.5*(attempt+1))
    return None

# ── D1 API ────────────────────────────────────────────────────────────────────
def d1_query(sql, params=None):
    """Führt eine SQL-Query gegen Cloudflare D1 aus."""
    body = json.dumps({"sql": sql, "params": params or []}).encode()
    req = urllib.request.Request(
        D1_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {CF_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read())
            if not result.get("success"):
                errors = result.get("errors", [])
                print(f"  D1 Fehler: {errors}")
                return None
            return result.get("result", [{}])[0]
    except Exception as e:
        print(f"  D1 Request Fehler: {e}")
        return None

def d1_batch(statements):
    """Führt mehrere SQL-Statements als Batch aus (effizienter)."""
    body = json.dumps(statements).encode()
    req = urllib.request.Request(
        D1_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {CF_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            result = json.loads(r.read())
            return result.get("success", False)
    except Exception as e:
        print(f"  D1 Batch Fehler: {e}")
        return False

def d1_init():
    """Erstellt die Tabellen falls nicht vorhanden."""
    print("→ Initialisiere D1 Datenbank…")
    schema = Path(__file__).parent / "db_init.sql"
    sql = schema.read_text()
    # Jedes Statement einzeln ausführen
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    for stmt in statements:
        result = d1_query(stmt)
        if result is None:
            print(f"  ✗ Fehler bei: {stmt[:60]}…")
            return False
    print("  ✓ Schema bereit")
    return True

# ── XML Parser ────────────────────────────────────────────────────────────────
def parse_xml(data, kuerzel):
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml_files = [f for f in z.namelist() if f.endswith(".xml")]
                if not xml_files: return {}
                xml_file = max(xml_files, key=lambda f: z.getinfo(f).file_size)
                data = z.read(xml_file)
        except: pass
    try:
        root = ET.fromstring(data)
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
            "titel": titel,
            "inhalt": inhalt[:8000],
        })
    return {"meta": meta, "paragraphen": paragraphen}

# ── TOC laden ────────────────────────────────────────────────────────────────
def load_toc():
    print("→ Lade gii-toc.xml…")
    data = fetch(TOC_URL)
    if not data: return {}
    root = ET.fromstring(data)
    toc = {}
    for item in root.iter("item"):
        title = (item.findtext("title") or item.get("title","")).strip()
        link  = (item.findtext("link")  or item.get("link","")).strip()
        if title and link:
            toc[title.upper()] = link
    print(f"  ✓ {len(toc)} Gesetze gefunden")
    return toc

# ── Gesetz in D1 schreiben ────────────────────────────────────────────────────
def upsert_gesetz(kuerzel, xml_url, name=""):
    kl = kuerzel.lower()
    print(f"  → {kuerzel}", end=" ", flush=True)

    data = fetch(xml_url) or fetch(f"{BASE_URL}/{kl}/xml.zip")
    if not data:
        print("✗ nicht erreichbar"); return False

    parsed = parse_xml(data, kuerzel)
    if not parsed.get("paragraphen"):
        print("✗ keine Paragraphen"); return False

    gesetz_name = name or parsed["meta"].get("langtitel", kuerzel)
    now = datetime.now(timezone.utc).isoformat()

    # Gesetz upsert
    d1_query(
        "INSERT INTO gesetze (kuerzel, name, quelle, abgerufen) VALUES (?,?,?,?) "
        "ON CONFLICT(kuerzel) DO UPDATE SET name=excluded.name, abgerufen=excluded.abgerufen",
        [kl, gesetz_name, f"{BASE_URL}/{kl}/", now]
    ) or d1_query(
        "INSERT OR REPLACE INTO gesetze (kuerzel, name, quelle, abgerufen) VALUES (?,?,?,?)",
        [kl, gesetz_name, f"{BASE_URL}/{kl}/", now]
    )

    # Paragraphen in Batches von 20
    paras = parsed["paragraphen"]
    batch_size = 20
    ok = 0
    for i in range(0, len(paras), batch_size):
        batch = paras[i:i+batch_size]
        statements = [
            {
                "sql": "INSERT INTO paragraphen (kuerzel, gesetz_name, bezeichnung, titel, inhalt) "
                       "VALUES (?,?,?,?,?) ON CONFLICT(kuerzel, bezeichnung) DO UPDATE SET "
                       "titel=excluded.titel, inhalt=excluded.inhalt",
                "params": [kl, gesetz_name, p["bezeichnung"], p["titel"], p["inhalt"]]
            }
            for p in batch
        ]
        if d1_batch(statements):
            ok += len(batch)
        time.sleep(0.1)

    print(f"✓ {ok}/{len(paras)} §§")
    return True

# ── Auch lokales data/ aktualisieren (für Fuse.js) ────────────────────────────
def update_local_index():
    """Erstellt schlankes index.json für Browser-Suche aus D1."""
    print("\n→ Aktualisiere lokalen Index aus D1…")
    result = d1_query(
        "SELECT kuerzel, gesetz_name, bezeichnung, titel FROM paragraphen ORDER BY kuerzel, bezeichnung"
    )
    if not result:
        print("  ✗ Konnte Index nicht laden"); return

    rows = result.get("results", [])
    index = [{"gesetz":r["kuerzel"],"gesetzName":r["gesetz_name"],"bezeichnung":r["bezeichnung"],"titel":r["titel"]} for r in rows]

    data_dir = Path(__file__).parent.parent / "data"
    data_dir.mkdir(exist_ok=True)
    with open(data_dir/"index.json","w",encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",",":"))

    meta = {
        "generiert": datetime.now(timezone.utc).isoformat(),
        "anzahl_eintraege": len(index),
        "gesetze": sorted({e["gesetz"] for e in index}),
    }
    with open(data_dir/"meta.json","w",encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"  ✓ {len(index)} Einträge · {len(meta['gesetze'])} Gesetze")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--test",   action="store_true", help="Nur GG+BGB+StGB")
    parser.add_argument("--gesetz", help="Einzelnes Gesetz (z.B. bgb)")
    parser.add_argument("--all",    action="store_true", help="Alle Gesetze")
    parser.add_argument("--init-only", action="store_true", help="Nur Schema anlegen")
    args = parser.parse_args()

    if not d1_init():
        sys.exit(1)

    if args.init_only:
        print("Schema angelegt."); return

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
        url = toc.get(k.upper(), f"{BASE_URL}/{k.lower()}/xml.zip")
        if upsert_gesetz(k, url):
            ok += 1
        time.sleep(0.3)

    print(f"\n✓ {ok}/{len(auswahl)} Gesetze in D1 geschrieben")
    update_local_index()

if __name__ == "__main__":
    main()
