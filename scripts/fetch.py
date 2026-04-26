#!/usr/bin/env python3
"""
Scraper für gesetze-im-internet.de
Lädt Gesetze als XML herunter und konvertiert sie zu JSON.

Verwendung:
  python fetch.py                  # Alle konfigurierten Gesetze
  python fetch.py --gesetz bgb     # Nur ein bestimmtes Gesetz
  python fetch.py --all            # Alle verfügbaren Gesetze (>6000, langsam)
"""

import json
import os
import re
import time
import argparse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
import urllib.request
import urllib.error

# ─── Konfiguration ────────────────────────────────────────────────────────────

BASE_URL = "https://www.gesetze-im-internet.de"
DATA_DIR = Path(__file__).parent.parent / "data"

# Starter-Set: die wichtigsten deutschen Gesetze
GESETZE_STARTER = {
    "gg":       "Grundgesetz",
    "bgb":      "Bürgerliches Gesetzbuch",
    "stgb":     "Strafgesetzbuch",
    "hgb":      "Handelsgesetzbuch",
    "zpo":      "Zivilprozessordnung",
    "stpo":     "Strafprozessordnung",
    "ao_1977":  "Abgabenordnung",
    "inso":     "Insolvenzordnung",
    "arbgg":    "Arbeitsgerichtsgesetz",
    "betrvg":   "Betriebsverfassungsgesetz",
    "kschg":    "Kündigungsschutzgesetz",
    "mabv":     "Makler- und Bauträgerverordnung",
    "ustg_1980":"Umsatzsteuergesetz",
    "estg":     "Einkommensteuergesetz",
    "gwg_2017": "Geldwäschegesetz",
    "bdsg_2018":"Bundesdatenschutzgesetz",
    "tmg":      "Telemediengesetz",
    "ttdsg":    "Telekommunikation-Telemedien-Datenschutz-Gesetz",
    "urhg":     "Urheberrechtsgesetz",
    "patg":     "Patentgesetz",
    "markg":    "Markengesetz",
    "uwg":      "Gesetz gegen unlauteren Wettbewerb",
    "gwb":      "Gesetz gegen Wettbewerbsbeschränkungen",
    "vwgo":     "Verwaltungsgerichtsordnung",
    "vwvfg":    "Verwaltungsverfahrensgesetz",
    "sgb_1":    "SGB I – Allgemeiner Teil",
    "sgb_5":    "SGB V – Krankenversicherung",
    "aufenthg_2004": "Aufenthaltsgesetz",
    "asylg":    "Asylgesetz",
    "stvzo":    "Straßenverkehrs-Zulassungs-Ordnung",
    "stvo":     "Straßenverkehrs-Ordnung",
}

# ─── HTTP Hilfsfunktionen ──────────────────────────────────────────────────────

def fetch_url(url: str, retries: int = 3, delay: float = 1.0) -> bytes | None:
    """Lädt eine URL mit Retry-Logik."""
    headers = {
        "User-Agent": "gesetze-de-repo/1.0 (github.com; open-source legal data)"
    }
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"  404 – nicht gefunden: {url}")
                return None
            print(f"  HTTP {e.code} bei {url}, Versuch {attempt+1}/{retries}")
        except Exception as e:
            print(f"  Fehler bei {url}: {e}, Versuch {attempt+1}/{retries}")
        if attempt < retries - 1:
            time.sleep(delay * (attempt + 1))
    return None

# ─── XML Parser ───────────────────────────────────────────────────────────────

def parse_gesetze_xml(xml_bytes: bytes, kuerzel: str) -> dict:
    """
    Parst das XML-Format von gesetze-im-internet.de.
    Struktur: <dokumente> → <norm> (enthält Metadaten + Paragraphen)
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        print(f"  XML-Fehler: {e}")
        return {}

    ns = ""
    # Namespace-Erkennung
    tag = root.tag
    if "{" in tag:
        ns = tag.split("}")[0] + "}"

    def t(name):
        return f"{ns}{name}"

    paragraphen = []
    meta = {}

    for norm in root.findall(f".//{t('norm')}"):
        # Metadaten der Norm
        metadaten = norm.find(t("metadaten"))
        text_el = norm.find(f".//{t('textdaten')}")

        if metadaten is None:
            continue

        enbez = metadaten.findtext(t("enbez"), "")    # z.B. "§ 823"
        titel = metadaten.findtext(t("titel"), "")    # Überschrift
        gliederung = metadaten.findtext(t("gliederungseinheit"), "")

        # Gesetz-Metadaten (erste Norm ohne enbez)
        if not enbez and not meta:
            meta = {
                "langtitel":  metadaten.findtext(t("langue"), ""),
                "kurztitel":  metadaten.findtext(t("jurabk"), kuerzel.upper()),
                "amtabk":     metadaten.findtext(t("amtabk"), ""),
                "ausfertigungsdatum": metadaten.findtext(t("ausfertigung-datum"), ""),
                "fundstelle":  metadaten.findtext(t("fundstelle"), ""),
            }
            continue

        if not enbez:
            continue

        # Textinhalt extrahieren
        inhalt_raw = ""
        if text_el is not None:
            # Alle Text-Elemente zusammenführen, HTML-Tags entfernen
            inhalt_raw = ET.tostring(text_el, encoding="unicode", method="text")
            inhalt_raw = re.sub(r"\s+", " ", inhalt_raw).strip()

        paragraphen.append({
            "bezeichnung": enbez,
            "titel":       titel,
            "gliederung":  gliederung,
            "inhalt":      inhalt_raw[:5000],  # max 5000 Zeichen pro Paragraph
        })

    return {
        "meta": meta,
        "paragraphen": paragraphen,
        "count": len(paragraphen),
    }

# ─── Einzelnes Gesetz laden ───────────────────────────────────────────────────

def fetch_gesetz(kuerzel: str, name: str) -> bool:
    """Lädt ein Gesetz von gesetze-im-internet.de und speichert es als JSON."""
    print(f"→ {kuerzel.upper()} – {name}")

    # XML-URL: https://www.gesetze-im-internet.de/{kuerzel}/xml.zip oder direkt
    xml_url = f"{BASE_URL}/{kuerzel}/xml.zip"
    data = fetch_url(xml_url)

    if data is None:
        # Fallback: direktes XML
        xml_url = f"{BASE_URL}/{kuerzel.lower()}/gesamt.xml"
        data = fetch_url(xml_url)

    if data is None:
        print(f"  ✗ Konnte {kuerzel} nicht laden")
        return False

    # ZIP entpacken falls nötig
    if data[:2] == b"PK":
        import zipfile, io
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml_files = [f for f in z.namelist() if f.endswith(".xml")]
                if not xml_files:
                    print(f"  ✗ Keine XML-Datei im ZIP für {kuerzel}")
                    return False
                # Größte XML-Datei nehmen (= Volltext)
                xml_file = max(xml_files, key=lambda f: z.getinfo(f).file_size)
                data = z.read(xml_file)
        except zipfile.BadZipFile:
            pass  # War kein ZIP, als XML weiterverarbeiten

    parsed = parse_gesetze_xml(data, kuerzel)
    if not parsed or not parsed.get("paragraphen"):
        print(f"  ✗ Keine Paragraphen gefunden für {kuerzel}")
        return False

    # Ausgabeverzeichnis
    out_dir = DATA_DIR / kuerzel.lower()
    out_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "kuerzel":   kuerzel.lower(),
        "name":      name,
        "quelle":    f"{BASE_URL}/{kuerzel}/",
        "abgerufen": datetime.now(timezone.utc).isoformat(),
        **parsed,
    }

    with open(out_dir / "data.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"  ✓ {parsed['count']} Paragraphen gespeichert")
    return True

# ─── Index aufbauen ───────────────────────────────────────────────────────────

def build_index():
    """Erstellt index.json mit allen Gesetzen (ohne Volltexte, nur Metadaten)."""
    print("\n→ Baue Suchindex...")
    index = []

    for gesetz_dir in sorted(DATA_DIR.iterdir()):
        data_file = gesetz_dir / "data.json"
        if not data_file.exists():
            continue

        with open(data_file, encoding="utf-8") as f:
            g = json.load(f)

        # Flache Liste für Fuse.js: ein Eintrag pro Paragraph
        for p in g.get("paragraphen", []):
            index.append({
                "gesetz":      g["kuerzel"],
                "gesetzName":  g["name"],
                "bezeichnung": p["bezeichnung"],
                "titel":       p["titel"],
                "inhalt":      p["inhalt"][:500],  # Kurzer Auszug für Suche
            })

    with open(DATA_DIR / "index.json", "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))

    print(f"  ✓ Index mit {len(index)} Einträgen erstellt")

    # Meta-Übersicht
    meta = {
        "generiert":   datetime.now(timezone.utc).isoformat(),
        "anzahl_eintraege": len(index),
        "gesetze": list({e["gesetz"] for e in index}),
    }
    with open(DATA_DIR / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

# ─── Hauptprogramm ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Gesetze-Scraper")
    parser.add_argument("--gesetz", help="Nur dieses Gesetz laden (Kürzel, z.B. bgb)")
    parser.add_argument("--all",    action="store_true", help="Alle ~6000 Gesetze")
    parser.add_argument("--no-index", action="store_true", help="Kein Index aufbauen")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if args.gesetz:
        name = GESETZE_STARTER.get(args.gesetz, args.gesetz.upper())
        fetch_gesetz(args.gesetz, name)
    elif args.all:
        # Gesamtliste von gesetze-im-internet.de laden
        print("Lade Gesamtliste...")
        data = fetch_url(f"{BASE_URL}/aktuell.html")
        if data:
            links = re.findall(r'/([a-z0-9_]+)/index\.html', data.decode("utf-8", errors="ignore"))
            gesetze_all = {k: k.upper() for k in set(links)}
            print(f"  {len(gesetze_all)} Gesetze gefunden")
            for i, (k, n) in enumerate(gesetze_all.items(), 1):
                print(f"[{i}/{len(gesetze_all)}]", end=" ")
                fetch_gesetz(k, n)
                time.sleep(0.5)  # Höflich gegenüber dem Server
    else:
        # Standard: Starter-Set
        ok = 0
        for i, (kuerzel, name) in enumerate(GESETZE_STARTER.items(), 1):
            print(f"[{i}/{len(GESETZE_STARTER)}]", end=" ")
            if fetch_gesetz(kuerzel, name):
                ok += 1
            time.sleep(0.3)
        print(f"\n✓ {ok}/{len(GESETZE_STARTER)} Gesetze erfolgreich geladen")

    if not args.no_index:
        build_index()

if __name__ == "__main__":
    main()
