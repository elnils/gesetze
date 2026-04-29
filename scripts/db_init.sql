-- Gesetze Tabelle
CREATE TABLE IF NOT EXISTS gesetze (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  kuerzel   TEXT NOT NULL,
  name      TEXT NOT NULL,
  quelle    TEXT,
  abgerufen TEXT
);

-- Paragraphen Tabelle  
CREATE TABLE IF NOT EXISTS paragraphen (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  gesetz_id   INTEGER NOT NULL REFERENCES gesetze(id),
  kuerzel     TEXT NOT NULL,
  gesetz_name TEXT NOT NULL,
  bezeichnung TEXT NOT NULL,
  titel       TEXT,
  inhalt      TEXT,
  UNIQUE(kuerzel, bezeichnung)
);

-- Urteile Tabelle
CREATE TABLE IF NOT EXISTS urteile (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  gericht    TEXT NOT NULL,
  datum      TEXT,
  aktenzeichen TEXT,
  titel      TEXT,
  leitsatz   TEXT,
  volltext   TEXT,
  quelle     TEXT,
  url        TEXT,
  rechtsgebiet TEXT
);

-- Volltext-Suche Index
CREATE INDEX IF NOT EXISTS idx_para_kuerzel     ON paragraphen(kuerzel);
CREATE INDEX IF NOT EXISTS idx_para_bezeichnung ON paragraphen(bezeichnung);
CREATE INDEX IF NOT EXISTS idx_urteil_datum     ON urteile(datum);
CREATE INDEX IF NOT EXISTS idx_urteil_gericht   ON urteile(gericht);
CREATE INDEX IF NOT EXISTS idx_urteil_rg        ON urteile(rechtsgebiet);
