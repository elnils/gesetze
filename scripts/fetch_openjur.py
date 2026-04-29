#!/usr/bin/env python3
"""
OpenJur Scraper → Cloudflare D1
Lädt Urteile von openjur.de und schreibt sie in D1.
API Docs: https://openjur.de/api/
"""

import json, os, re, time, sys
from datetime import datetime, timezone
from pathlib import Path
import urllib.request, urllib.error

CF_TOKEN   = os.environ["CLOUDFLARE_API_TOKEN"]
CF_ACCOUNT = os.environ["CLOUDFLARE_ACCOUNT_ID"]
CF_DB_ID   = os.environ["CLOUDFLARE_D1_DATABASE_ID"]
D1_URL     = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/d1/database/{CF_DB_ID}/query"

OPENJUR_API = "https://openjur.de/api"
HEADERS = {"User-Agent": "gesetze-de-repo/3.0 (github.com; open-source; legal research)"}

# Wichtigste Rechtsgebiete
RECHTSGEBIETE = [
    ("Zivilrecht",        "civil"),
    ("Strafrecht",        "criminal"),
    ("Verwaltungsrecht",  "administrative"),
    ("Arbeitsrecht",      "labour"),
    ("Steuerrecht",       "tax"),
    ("Sozialrecht",       "social"),
    ("Verfassungsrecht",  "constitutional"),
]

def fetch_json(url):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  Fehler: {e}")
        return None

def d1_query(sql, params=None):
    body = json.dumps({"sql": sql, "params": params or []}).encode()
    req = urllib.request.Request(
        D1_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {CF_TOKEN}", "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read())
            if not result.get("success"):
                return None
            return result.get("result", [{}])[0]
    except Exception as e:
        print(f"  D1 Fehler: {e}"); return None

def d1_batch(statements):
    body = json.dumps(statements).encode()
    req = urllib.request.Request(
        D1_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {CF_TOKEN}", "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read()).get("success", False)
    except Exception as e:
        print(f"  D1 Batch Fehler: {e}"); return False

def fetch_urteile(rechtsgebiet_de, rechtsgebiet_en, limit=200):
    """Lädt Urteile eines Rechtsgebiets von OpenJur."""
    print(f"  → {rechtsgebiet_de}", end=" ", flush=True)

    # OpenJur Suche nach Rechtsgebiet
    url = f"{OPENJUR_API}/urteile/?format=json&rechtsgebiet={rechtsgebiet_en}&limit=100&ordering=-datum"
    data = fetch_json(url)
    if not data:
        print("✗ keine Daten"); return 0

    urteile = data.get("results", data if isinstance(data, list) else [])
    if not urteile:
        print("✗ leer"); return 0

    ok = 0
    batch = []
    for u in urteile[:limit]:
        # Felder extrahieren (OpenJur hat verschiedene Strukturen)
        gericht     = u.get("gericht", u.get("court", ""))
        datum       = u.get("datum", u.get("date", ""))
        az          = u.get("aktenzeichen", u.get("reference", u.get("az", "")))
        titel       = u.get("titel", u.get("title", ""))
        leitsatz    = u.get("leitsatz", u.get("abstract", ""))
        volltext    = u.get("inhalt", u.get("text", u.get("content", "")))
        source_url  = u.get("url", u.get("link", ""))

        if not gericht: continue

        # Volltext kürzen
        if volltext: volltext = volltext[:10000]
        if leitsatz: leitsatz = leitsatz[:2000]

        batch.append({
            "sql": "INSERT INTO urteile (gericht, datum, aktenzeichen, titel, leitsatz, volltext, quelle, url, rechtsgebiet) "
                   "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            "params": [str(gericht), str(datum), str(az), str(titel),
                      str(leitsatz), str(volltext) if volltext else "",
                      "openjur.de", str(source_url), rechtsgebiet_de]
        })

        if len(batch) >= 20:
            if d1_batch(batch): ok += len(batch)
            batch = []
            time.sleep(0.1)

    if batch:
        if d1_batch(batch): ok += len(batch)

    print(f"✓ {ok} Urteile")
    return ok

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Nur Zivilrecht")
    args = parser.parse_args()

    gebiete = [RECHTSGEBIETE[0]] if args.test else RECHTSGEBIETE
    print(f"\n[OpenJur: {len(gebiete)} Rechtsgebiete]\n")

    total = 0
    for de, en in gebiete:
        total += fetch_urteile(de, en)
        time.sleep(1)

    # Anzahl in D1 prüfen
    result = d1_query("SELECT COUNT(*) as cnt FROM urteile")
    if result:
        cnt = result.get("results", [{}])[0].get("cnt", "?")
        print(f"\n✓ {total} neue Urteile · {cnt} gesamt in D1")

if __name__ == "__main__":
    main()
