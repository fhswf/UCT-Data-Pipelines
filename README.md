# Data-Pipeline – Urban Climate Twin Soest

Die Data-Pipeline lädt Wetter- und Fernerkundungsdaten, bereitet sie für das Data Warehouse auf und importiert sie in eine PostgreSQL-Datenbank.

## Urban Climate Twin Soest

 [Urban Climate Twin (UCT) Soest](https://www.fh-swf.de/de/forschung___transfer_4/forschungsprojekte_1/forschungsprojekt_uct.php) ist ein digitaler Zwilling des Soester Stadtklimas. Er führt Messdaten, Geodaten und weitere Klimadaten zusammen und macht das Stadklima räumlich nachvollziehbar. Bürger und die Stadtverwaltung können so die Auswirkungen von Klimaveränderungen auf das Stadtgebiet besser verstehen und Maßnahmen zur Anpassung an den Klimawandel entwickeln.


## Datenquellen und Verarbeitung

- **DWD:** 10-Minuten-Messwerte ausgewählter Stationen sowie stündliche Wetterberichte. Die 10-Minuten-Temperaturdaten werden zusätzlich geglättet.
- **ECOSTRESS:** satellitengestützte Landoberflächentemperatur- und Qualitätsdaten.
- **Landsat:** ausgewählte Landsat-Szenen und zugehörige Temperatur-, Qualitäts- und Metadaten.

Die Skripte transformieren die Daten in das verwendete Data-Warehouse-Schema und schreiben sie in PostgreSQL.

## Projektstruktur

```text
/
├── config/
│   ├── bounding_box.json             # Untersuchungsgebiet für ECOSTRESS
│   └── bounding_box_soest/           # Shapefile für räumliche Landsat-Verarbeitung
├── utils/
│   ├── common_utils.py               # Logging und Umgebungsvariablen
│   ├── dwd_utils.py                  # DWD-Abruf, Aufbereitung und Glättung
│   ├── ecostress_utils.py            # ECOSTRESS-Abruf und -Verarbeitung
│   └── landsat_utils.py              # Landsat-Abruf und -Verarbeitung
├── run_dwd_10.py                     # DWD-10-Minuten-Pipeline
├── run_dwd_wr.py                     # DWD-Wetterbericht-Pipeline
├── run_ecostress.py                  # ECOSTRESS-Pipeline
├── run_landsat.py                    # Landsat-Pipeline
├── .env.example                      # Vorlage für lokale Konfiguration
└── requirements.txt                  # Python-Abhängigkeiten
```
## Deployment
### Voraussetzungen

- Python 3.12+
- PostgreSQL mit PostGIS
- Zugangsdaten für die verwendeten Datenquellen:
  - ECOSTRESS/AppEEARS: NASA-Earthdata-Konto
  - Landsat: USGS-Konto mit API-Zugang
  - FieldClimate: öffentliche und private API-Schlüssel
- Zugriff auf das konfigurierte Data Warehouse sowie die benötigten Tabellen und Schemata

### Umgebungsvariablen

| Variable | Pflicht | Beschreibung |
| --- | --- | --- |
| `DB_HOST` | Ja | Hostname der PostgreSQL-Instanz |
| `DB_PORT` | Ja | Port der PostgreSQL-Instanz |
| `DB_NAME` | Ja | Datenbankname |
| `DB_USER` | Ja | Datenbanknutzer |
| `DB_PASSWORD` | Ja | Passwort des Datenbanknutzers |
| `TABLE_NAME` | Ja | Zieltabelle für die Messdaten |
| `SCHEMA_NAME` | Ja | Datenbankschema der Zieltabelle |
| `ECO_USER` | Für ECOSTRESS | Benutzername des NASA-Earthdata-Kontos |
| `ECO_PASSWORD` | Für ECOSTRESS | Passwort des NASA-Earthdata-Kontos |
| `ERS_USERNAME` | Für Landsat | USGS-Benutzername |
| `ERS_TOKEN` | Für Landsat | USGS-API-Token |
