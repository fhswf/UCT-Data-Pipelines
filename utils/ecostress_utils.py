import os
import pandas as pd
import time
import json
import logging
import re
from datetime import datetime, timedelta

import requests
import numpy as np
import geopandas as gpd
from shapely.geometry import box
import rasterio
import sqlalchemy
from sqlalchemy import create_engine, text
from sqlalchemy.types import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
import geoalchemy2
from geoalchemy2.types import Geometry


# logger initialisieren
log = logging.getLogger("ecostress")

# Setup-Überprüfung
def check_setup(directories: list[str]) -> None:
    """
    Überprüft, ob die benötigten Verzeichnisse existieren und erstellt sie bei Bedarf.

    Args:
        directories (list[str]): Liste der Verzeichnisse, die überprüft und ggf. erstellt werden sollen.
    """
    for directory in directories:
        os.makedirs(directory, exist_ok=True)
    log.debug("Setup überprüft.")

# Verbindung zur Datenbank herstellen
def connect_to_dw(user : str, password : str, host : str, port : int, db_name : str) -> sqlalchemy.engine.base.Engine:
    """
    Erstellt eine Verbindung zur Datenbank mit SQLAlchemy.

    Args:
        user (str): Benutzername für die Datenbank.
        password (str): Passwort für die Datenbank.
        host (str): Hostname der Datenbank.
        port (int): Port der Datenbank.
        db_name (str): Name der Datenbank.
    """
    try:
        # Verbindung mit SQLAlchemy erstellen
        ENGINE = create_engine(f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db_name}")
        log.info("Verbindung zur Datenbank erfolgreich hergestellt!")
        return ENGINE
    except Exception as e:
        log.error(f"Fehler beim Verbinden mit der Datenbank: {e}")
        raise

# API Call
def get_api_token(username : str, password : str) -> str:
    """
    Ruft den API-Token von AppEEARS ab.

    Args:
        username (str): Benutzername für die AppEEARS-API.
        password (str): Passwort für die AppEEARS-API.
    """
    log.debug("Starte Token-Abruf...")
    try:
        get_token = requests.post('https://appeears.earthdatacloud.nasa.gov/api/login', auth=(username, password))
        token_response = get_token.json()
        token = token_response['token']
        log.info("Token erfolgreich abgerufen.")
    except requests.exceptions.RequestException as e:
        log.error(f"FEHLER beim Abrufen des Tokens: {e}")
        raise

    return token

def get_last_scene_date(engine : sqlalchemy.engine.base.Engine,quelle: str = "Ecostress", table : str = "public.messungen") -> datetime:
    """
    Ruft das letzte Szenen-Datum aus der Datenbank ab. Wenn kein Datum gefunden wird, wird der Standardwert 2020-01-01 zurückgegeben.
    
    Args:
        engine (sqlalchemy.engine.base.Engine): SQLAlchemy engine für die Verbindung zur Datenbank.
        quelle (str): Quelle der Daten | Default
        table (str): Tabellenname | Default = "public.messungen".
    
    Returns:
        datetime: das Datum der letzen Verarbeiteten Szene aus der Datenbank.
    """
    log.debug("Starte Abfrage des letzten Szenen-Datums...")
    sql = text(f"SELECT MAX(messzeit_utc) FROM {table} WHERE quell_typ = '{quelle}'")
    try:
        with engine.connect() as connection:
            last_scene_date = connection.execute(sql).scalar() 
    except Exception as e:
        log.error(f"FEHLER beim Ausführen der Abfrage: {e}")
        raise

    if last_scene_date is None:
        last_scene_date = datetime(2020, 1, 1)
    
    log.info(f"Letztes Szenen-Datum: {last_scene_date}")

    return last_scene_date

def get_timerange(start_time : datetime) -> tuple[str, str, str]:
    """
    Berechnet den Zeitraum für die Abfrage neuer Szenen und bringt die Zeitstempel in das richtige Format.
    Auf das Startdatum wird ein Tag addiert, um nach neuen Szenen zu suchen und Duplikate zu vermeiden.

    Args:
        start_time (datetime): Startdatum der Anfrage

    Returns:
        tuple: Tupel mit drei Zeitstempeln:
            - str: Zeitraum     | Format: "YYYY-MM-DDTHH:MM:SSZ,YYYY-MM-DDTHH:MM:SSZ".
            - str: Startdatum   | Format: "MM-DD-YYYY".
            - str: Enddatum     | Format: "MM-DD-YYYY".
    """
    end_time = datetime.now().replace(second=0, microsecond=0)

    start_time = start_time + timedelta(days=1) # search for new scenes
    start_time_formatted = start_time.strftime("%m-%d-%Y")
    end_time_formatted = end_time.strftime("%m-%d-%Y")

    return f"{start_time.isoformat()}Z,{end_time.isoformat()}Z", start_time_formatted, end_time_formatted

def scene_search(timerange : str, bounding_box : str = "7.87339481,51.51405699,8.17560703,51.62038395") -> bool:
    """
    Sucht bei NASA CMR Earthdata nach neuen Ecostress-Szenen im angegebenen Zeitbereich über dem Kreis Soest.
    Es wird geloggt, wie viele Szenen gefunden wurden.

    Args:
        timerange (str): Zeitbereich für die Suche nach neuen Szenen.
        bounding_box (str): Bounding Box für die Suche. Standard: "7.87339481,51.51405699,8.17560703,51.62038395".

    Returns:
        bool: True, wenn Szenen gefunden wurden, sonst False.
    """
    log.debug(f"Suche nach neuen Szenen zwischen {timerange}.")
    
    url = "https://cmr.earthdata.nasa.gov/search/granules.json"
    params = {
        "collection_concept_id": "C2076090826-LPCLOUD",
        "bounding_box": bounding_box,
        "temporal": timerange,
        "page_size": 200,
        "page_num": 1
    }

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        granules = response.json().get("feed", {}).get("entry", [])
        if len(granules) == 0:
            log.info("Keine neuen Granules gefunden.")
            log.info("Skript wird beendet.")
            return False
        log.info(f"{len(granules)} neue Granules gefunden")
        return True
    except requests.RequestException as e:
        log.error(f"Fehler beim abrufen von neuen Granules: {e}")
        return False

def create_request(start_date : datetime, end_date: datetime, token : str, bb_path : str) -> str:
    """
    Erstellt ein Request mit der AppEEARS API, um Szenen im angegebenen Zeitbereich abzurufen.

    Args:
        start_date (datetime): Startdatum für die Abfrage.
        end_date (datetime): Enddatum für die Abfrage.
        token (str): API-Token für die Authentifizierung.
        bb_path (str): Pfad zur JSON-Datei mit der Bounding Box.

    Returns:
        str: Task-ID der Anfrage, die für das Tracking und den Download der Daten verwendet wird.
    """
    log.debug(f"Erstelle Request für Szenen zwischen {start_date} und {end_date} bei AppEEARS.")

    with open(bb_path, "r") as file:
        bounding_box = json.load(file)

    task = {
        "params": {
            "geo": {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [bounding_box]
                    },
                    "properties": {}
                }]
            },
            "dates": [{
                "endDate": f"{end_date}",
                "startDate": f"{start_date}",
                "yearRange": [2000, 2050]
            }],
            "layers": [
                {"layer": "LST", "product": "ECO_L2T_LSTE.002"}
            ],
            "output": {
                "format": {"type": "geotiff"},
                "projection": "native",
            }
        },
        "task_name": f"Data Pipeline Task {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "task_type": "area"
    }

    try:
        response = requests.post(
            'https://appeears.earthdatacloud.nasa.gov/api/task',
            json=task,
            headers={'Authorization': f'Bearer {token}'}
        )
        response.raise_for_status()
        task_response = response.json()
        log.info(f"Request erfolgreich erstellt: {task_response}")
        return task_response['task_id']
    except requests.exceptions.RequestException as e:
        log.error(f"FEHLER beim Erstellen des Requests: {e}")
        raise

def task_status_polling(task_id : str, token : str, polling_interval : int =60) -> bool:
    """
    Polling zur zyklischen Abfrage des Task-Status. Gibt True zurück, wenn der Task abgeschlossen ist. 
    Andernfalls wird alle 60 Sekunden der Status abgefragt. Nach 60 Minuten wird ein Timeout ausgelöst.

    Args:
        task_id (str): Task-ID des Requests.
        token (str): API-Token für die Authentifizierung.
        polling_interval (int): Intervall für die Polling-Abfrage in Sekunden (Standard: 60).

    Returns:
        bool: True, wenn der Task abgeschlossen ist.

    Raises:
        TimeoutError: Wenn der Task nicht innerhalb von 60 Minuten abgeschlossen ist.
        requests.RequestException: Wenn während der API-Anforderung ein Fehler auftritt.
    """
    url = f'https://appeears.earthdatacloud.nasa.gov/api/task/{task_id}'
    headers = {'Authorization': f'Bearer {token}'}
    count = 0
    while True:
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            status = response.json().get('status')
            log.debug(f"Task-Status: {status}")
            if status == "done":
                log.info(f"Task abgeschlossen. (Took {count * polling_interval} seconds)")
                return True
        except requests.RequestException as e:
            log.error(f"Fehler beim abrufen des Task-Status: {e}")
        time.sleep(polling_interval)
        count += 1
        if count > 60:  # Timeout nach 60 Minuten
            log.error("Polling Timeout erreicht. Task nicht abgeschlossen.")
            raise TimeoutError("Polling Timeout erreicht. Task nicht abgeschlossen.")

def download_data(task_id : str, data_dir : str, token : str) -> None:
    """
    Nachdem der Task abgeschlossen ist, werden die benötigten Daten (LST- und QC-Dateien) des Tasks heruntergeladen und in das DATA_DIR verschoben.
    Schritte:
    1. Abfrage des Task-Status durch Polling.
    2. Abrufen der verfügbaren Dateien.
    3. Filtern der Dateien, um nur benötigte herunterzuladen („_LST_“ oder „_QC_“).
    4. Herunterladen der gefilterten Dateien und Speichern im Verzeichnis DATA_DIR.
    5. Verschieben des Inhalts aus Unterordnern in das Hauptverzeichnis und Löschen der Unterordner.

    Args:
        task_id (str): Die Task-ID des Requests.
        data_dir (str): Das Verzeichnis, in dem die heruntergeladenen Dateien gespeichert werden.
        token (str): Der API-Token für die Authentifizierung.

    Raises:
        requests.RequestException: Wenn während der API-Anforderung für das Bundle oder den Dateidownload ein Fehler auftritt.
        Exception: Wenn beim Verschieben oder Löschen von Dateien und Verzeichnissen ein Fehler auftritt.
    """
    if task_status_polling(task_id, token):
        try:
            # Hole Dateinamen und IDs aus ganzen Task-Bundle
            response = requests.get(
                f'https://appeears.earthdatacloud.nasa.gov/api/bundle/{task_id}',
                headers={'Authorization': f'Bearer {token}'}
            )
            response.raise_for_status()
            bundle_response = response.json()
        except requests.RequestException as e:
            log.error(f"Error downloading data: {e}")
            raise

        files = {} # Speichere Dateinamen und IDs in dict
        for file_info in bundle_response['files']:
            file_name = file_info['file_name']
            if "_LST_" in file_name or "_QC_" in file_name: # Nur LST und QC Dateien herunterladen
                file_id = file_info['file_id']
                files[file_name] = file_id

    if not files:
        log.info("Keine Dateien zum Herunterladen gefunden.")
        return
    log.info(f"Lade {len(files)} Dateien herunter...")
    for file_name, file_id in files.items():  # Lade Dateien herunter
        try:
            response = requests.get( 
                'https://appeears.earthdatacloud.nasa.gov/api/bundle/{0}/{1}'.format(task_id,file_id),  
                headers={'Authorization': 'Bearer {0}'.format(token)}, 
                allow_redirects=True,
                stream=True
            ) 
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            log.error(f"FEHLER beim Herunterladen der Datei {file_name}: {e}")
            continue

        filepath = os.path.join(data_dir, file_name)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        with open(filepath, 'wb') as f:
            for data in response.iter_content(chunk_size=8192):
                f.write(data)
                
    log.info(f"{len(files)} Dateien erfolgreich heruntergeladen.")

    subfolder = next(
        (os.path.join(data_dir, d) for d in os.listdir(data_dir) if os.path.isdir(os.path.join(data_dir, d))),
        None
    )

    if subfolder:
        for file_name in os.listdir(subfolder):
            source = os.path.join(subfolder, file_name)
            destination = os.path.join(data_dir, file_name)
            if os.path.isfile(source):
                try:
                    os.rename(source, destination)
                except Exception as e:
                    log.error(f"Fehler beim verschieben von {file_name}: {e}")
                    raise

        try:
            os.rmdir(subfolder)
        except Exception as e:
            log.error(f"Fehler beim Löschen von Verzeichnis {subfolder}: {e}")
            raise

def group_files(dir : str) -> dict:
    """
    Gruppiert Dateien im angegebenen Verzeichnis basierend auf ihren Dateinamen.
    Für jede gefundene LST-Datei wird geprüft, ob eine zugehörige QC-Datei existiert. Wenn beide Dateien vorhanden sind, 
    werden sie als Gruppe in einem Dictionary gespeichert. Falls die QC-Datei fehlt, wird eine Warnung ausgegeben.

    Args:
        dir (str): Verzeichnis, in dem die LST- und QC-Dateien gespeichert sind.

    Returns:
        dict: Ein Dictionary, in dem die Schlüssel die Namen der LST-Dateien sind und die Werte die zugehörigen Pfade zu LST- und QC-Dateien.
        Beispiel:
        {"example_LST_20230101.tif": {"LST": "/path/to/DATA_DIR/example_LST_20230101.tif","QC": "/path/to/DATA_DIR/example_QC_20230101.tif"}}
    """
    log.debug("Gruppiere Dateien...")
    file_groups = {}

    for file_name in os.listdir(dir):
        if "_LST_" in file_name and file_name.endswith(".tif"):
            base_name = file_name.split("_LST_")[0]
            qc_file_name = f"{base_name}_QC_{file_name.split('_LST_')[1]}"
            
            lst_path = os.path.join(dir, file_name)
            qc_path = os.path.join(dir, qc_file_name)

            if os.path.exists(qc_path):
                file_groups[file_name] = {
                    "LST": lst_path,
                    "QC": qc_path
                }
            else:
                log.warning(f"QC-Datei für {file_name} nicht gefunden.")
        
    verbleibende_dateien = len(file_groups) * 2 - len(os.listdir(dir))

    log.info(f"{len(file_groups)} Dateigruppen erfolgreich erstellt. {'Verbleibende Dateien: ' + str(verbleibende_dateien) if verbleibende_dateien > 0 else ''}")
    return file_groups

def get_scene_datetime(file_path : str) -> str:
    """
    Extrahiert das Datum und die Uhrzeit aus dem Dateinamen einer LST-Datei.

    Args:
        file_path (str): Der Pfad zur LST-Datei.

    Returns:
        str: Das Datum und die Uhrzeit im Format "YYYY-MM-DD HH:MM:SS.sss", 
             oder None, falls kein gültiges Muster gefunden wurde.
    """
    # Extrahiere das Datum und die Uhrzeit aus dem Dateinamen
    pattern = re.compile(r"doy(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})")
    match = pattern.search(file_path)

    if match:
        year, doy, hour, minute, second = map(int, match.groups())
        date = datetime(year, 1, 1) + timedelta(days=doy - 1)
        return datetime(date.year, date.month, date.day, hour, minute, second).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    else:
        log.warning(f"Kein gültiges DOY-Muster in {file_path} gefunden!")
        return None

def interpret_qc_value(qc_value : int) -> str:
    """
    Interpretiert den QC-Wert und gibt die Qualität der LST-Daten zurück. 
    Die Qualität wird aus den letzten 2 Bits des QC-Wertes abgeleitet (siehe Ecostress-Dokumentation).

    Args:
        qc_value (int): Der QC-Wert.

    Returns:
        str: Die Qualität der LST-Daten als Bitdarstellung ("00", "01", "10", "11").
    """
    qc_value = np.uint16(qc_value)  # Ensure qc_value is cast to uint16
    qc_value_bin = np.binary_repr(qc_value, width=16)  # Convert to 16-bit binary representation
    lst_accuracy = qc_value_bin[-16:-14]  # Extract the relevant bits

    return lst_accuracy

def get_quelle(dw_connection : sqlalchemy.engine.base.Engine) -> gpd.GeoDataFrame:
    """
    Lädt die Tabelle 'quelle' aus der Datenbank und gibt sie als GeoDataFrame zurück. 
    Dabei werden die Geometriespalten 'location' und 'area_validity' zusammengeführt, 
    die Spalten umbenannt und nicht benötigte Spalten entfernt.

    Args:
        dw_connection (sqlalchemy.engine.base.Engine): SQLAlchemy-Engine für die Datenbankverbindung.

    Returns:
        gpd.GeoDataFrame: GeoDataFrame mit den Daten aus der Tabelle 'quelle'.
    """
    log.debug("Lade Quelle aus der Datenbank...")

    query = """
        SELECT q.id, l.area_validity 
        FROM public.quelle q
        JOIN public.location l ON q.raster_id = l.id
        WHERE q.typ = 'Ecostress'
          AND l.from_date <= NOW()
          AND l.until_date >= NOW()
    """

    quelle = gpd.read_postgis(query, dw_connection, geom_col='area_validity')
    log.info("Quelle erfolgreich geladen.")
    return quelle

def get_valid_pixel_mask(raster_path : str) -> np.ndarray:
    """
    Erstellt eine Maske für gültige Pixel in einem Rasterbild, indem NaN-Werte ausgeschlossen werden. 
    Die Maske gibt an, welche Pixel gültig (True) und welche ungültig (False) sind.

    Args:
        raster_path (str): Der Pfad zur Rasterdatei.

    Returns:
        np.ndarray: Eine Maske, die gültige Pixel (True) und ungültige Pixel (False) angibt.
    """
    with rasterio.open(raster_path) as src:
        image = src.read(1)
        return ~np.isnan(image)

def extract_valid_pixels(raster_path : str, transform, value_func, valid_mask=None) -> list:
    """
    Extrahiert gültige Pixel aus einem Rasterbild und gibt eine Liste von Polygonen und Werten zurück.

    Args:
        raster_path (str): Pfad zur Rasterdatei.
        transform: Raster-Transformationsparameter.
        value_func: Funktion zur Formatierung des Wertes => LST: lambda v: {"temperature": {"value": v - 273.15}} 
                    oder QC: lambda v: {"quality": interpret_qc_value(v)}.
        valid_mask (np.ndarray, optional): Maske, die angibt, welche Pixel gültig (True) und welche ungültig (False) sind. 
                                           Standard: None, wird nur benötigt, wenn QC-Daten verarbeitet werden.

    Returns:
        list: Eine Liste von Polygonen und Werten der LST-Aufnahme.
    """
    with rasterio.open(raster_path) as src:
        image = src.read(1)
        rows, cols = image.shape
        data = []

        for row in range(rows):
            for col in range(cols):
                if valid_mask is not None and not valid_mask[row, col]:
                    continue
                value = image[row, col]
                if not np.isnan(value):
                    minx, miny = transform * (col, row)
                    maxx, maxy = transform * (col + 1, row + 1)
                    geom = box(minx, miny, maxx, maxy)
                    data.append((geom, value_func(value)))
    return data

def process_scene(lst_path : str, qc_path : str, scene_datetime : str) -> tuple:
    """
    Diese Funktion verarbeitet eine einzelne Szene, indem sie die LST- und QC-Daten extrahiert und in ein GeoDataFrame umwandelt.

    Args:
        lst_path (str): Pfad zur LST-Datei.
        qc_path (str): Pfad zur QC-Datei.
        scene_datetime (str): Datum und Uhrzeit der Szene.

    Returns:
        tuple: Ein Tuple bestehend aus Polygonen, Werten, Zeitstempeln, Qualitätsdaten und dem Koordinatensystem (CRS).
    """
    with rasterio.open(lst_path) as lst_src:
        transform = lst_src.transform
        crs = lst_src.crs

    valid_mask = get_valid_pixel_mask(lst_path)

    temperature_data = extract_valid_pixels(lst_path, transform, lambda v: {"temperature": {"value": json.loads(str(v - 273.15))}}, valid_mask)
    quality_data = extract_valid_pixels(qc_path, transform, lambda v: {"quality": interpret_qc_value(v)}, valid_mask)

    polygons = [geom for geom, _ in temperature_data]
    values = [val for _, val in temperature_data]
    timestamps = [scene_datetime] * len(polygons)

    # Qualität zuordnen
    qualities = []
    for _, quality in quality_data:
        quality_value = quality.get("quality")
        formatted_value = f'{{"quality": "{quality_value}"}}'
        qualities.append(formatted_value)
    log.debug(f"{len(temperature_data)} Polygone extrahiert.")
    return polygons, values, timestamps, qualities, crs


def create_geodataframe(polygons : list, values : list, timestamps : list, qualities: list, crs : str) -> gpd.GeoDataFrame:
    """
    Erstellt ein GeoDataFrame aus den extrahierten Listen von Polygonen, Werten, Zeitstempeln und Qualitätsdaten.

    Args:
        polygons (list): Eine Liste von Polygonen.
        values (list): Eine Liste von Werten.
        timestamps (list): Eine Liste von Zeitstempeln.
        qualities (list): Eine Liste von Qualitätsdaten.
        crs (str): Das Koordinatensystem (CRS) der Geometrien.

    Returns:
        gpd.GeoDataFrame: Ein GeoDataFrame mit den extrahierten Daten.
    """
    gdf = gpd.GeoDataFrame({
        'geometry': polygons,
        'value': values,
        'timestamp': timestamps,
        'quality': qualities
    }, crs=crs, geometry='geometry')
    return gdf

def spatial_join_with_quelle(gdf : gpd.GeoDataFrame, quelle: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Führt einen spatial join zwischen dem GeoDataFrame und der Quelle durch, um die Polygone in Beziehung zu setzen.
    """
    if quelle.crs != gdf.crs:
        quelle = quelle.to_crs(gdf.crs)

    quelle = quelle.copy()
    quelle['raster_geom'] = quelle.geometry

    gdf = gdf.copy()
    gdf['centroid'] = gdf['geometry'].centroid
    gdf_centroids = gdf.set_geometry('centroid')

    joined = gpd.sjoin(gdf_centroids, quelle, how="inner", predicate="within")

    joined = joined.set_geometry('geometry').drop(columns=['centroid', 'index_right'], errors='ignore')

    discarded = len(gdf) - len(joined)
    if discarded > 0:
        log.info(f"{discarded} Pixel ohne Rasterzellen-Treffer verworfen.")

    return joined

def process_files(filegroup : dict, dw_connection : sqlalchemy.engine.base.Engine, target_crs : str = '25832') -> gpd.GeoDataFrame:
    """
    Verarbeitet alle Dateien in der Dateigruppe und erstellt ein GeoDataFrame mit den extrahierten Daten.

    Args:
        filegroup (dict): Ein Dictionary mit den Dateigruppen.
        dw_connection (sqlalchemy.engine.base.Engine): SQLAlchemy-Engine für die Datenbankverbindung.
        target_crs (str): Ziel-Koordinatensystem (CRS) für die Geometrien. Standard: '25832'.

    Returns:
        gpd.GeoDataFrame: Ein GeoDataFrame mit den extrahierten Daten.
    """
    all_polygons, all_values, all_timestamps, all_qualities = [], [], [], []

    log.info(f"Starte Verarbeitung von {len(filegroup)} Dateigruppen...")

    count = 1
    for group in filegroup.values():

        log.debug(f"Verarbeite Dateigruppe: {count} / {len(filegroup)}")
        count += 1

        lst_path = group["LST"]
        qc_path = group["QC"]
        scene_datetime = get_scene_datetime(lst_path)

        if scene_datetime is None:
            log.error(f"Ungültiges Datum für {lst_path}.")
            continue

        polygons, values, timestamps, qualities, crs = process_scene(lst_path, qc_path, scene_datetime)

        all_polygons.extend(polygons)
        all_values.extend(values)
        all_timestamps.extend(timestamps)
        all_qualities.extend(qualities)

    gdf = create_geodataframe(all_polygons, all_values, all_timestamps, all_qualities, crs)
    gdf = gdf.to_crs(epsg=target_crs)

    quelle = get_quelle(dw_connection)
    gdf = spatial_join_with_quelle(gdf, quelle)

    return gdf

def format_gdf(gdf : gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Formatiert das GeoDataFrame für den Upload in die Datenbank.
    - Konvertiert die Geometriespalte in das WKT-Format.
    - Benennt die Spalten entsprechend um.
    - Setzt die Geometriespalte auf 'location'.
    - Setzt das CRS auf EPSG:25832.

    Args:
        gdf (gpd.GeoDataFrame): Das GeoDataFrame, das formatiert werden soll.

    Returns:
        gpd.GeoDataFrame: Das formatierte GeoDataFrame.
    """
    df = pd.DataFrame(gdf).copy()
    df['messzeit_utc'] = df['timestamp']
    df['quell_id'] = df['id']
    df['measurement_data'] = df['value']
    df['additional_parameters'] = df.get('quality', None) 

    return df[['messzeit_utc', 'quell_id', 'measurement_data', 'additional_parameters']]

def clean_up(dir : str) -> None:
    """
    Löscht alle Dateien im Verzeichnis DATA_DIR.

    Args:
        dir (str): Verzeichnis, in dem die Dateien gelöscht werden sollen.

    Raises:
        Exception: Wenn beim Löschen der Dateien ein Fehler auftritt.
    """
    log.debug("Lösche alle Dateien im Datenverzeichnis...")
    for file_name in os.listdir(dir):
        file_path = os.path.join(dir, file_name)
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
        except Exception as e:
            log.error(f"FEHLER beim Löschen der Datei {file_name}: {e}")
            raise

    log.info("Löschen der Dateien abgeschlossen.")

def upload_to_dw(gdf : gpd.GeoDataFrame, engine : sqlalchemy.engine.base.Engine, schema : str, table : str) -> None:
    """
    Lädt das GeoDataFrame in die Datenbank hoch.

    Args:
        gdf (gpd.GeoDataFrame): Das GeoDataFrame, das hochgeladen werden soll.
        engine (sqlalchemy.engine.base.Engine): SQLAlchemy-Engine für die Datenbankverbindung.
        table (str): Name der Tabelle, in die die Daten hochgeladen werden sollen.

    Raises:
        Exception: Wenn beim Hochladen der Daten ein Fehler auftritt.
    """
    log.debug("Starte Upload in die Datenbank...")
    dtype = {
        'messzeit_utc': DateTime,
        'quell_id': String,
        'measurement_data': JSONB,
        'additional_parameters': JSONB
    }
    try:
        df = pd.DataFrame(gdf)
        for col in ['measurement_data', 'additional_parameters']:
            df[col] = df[col].apply(lambda x: json.loads(x) if isinstance(x, str) else x)

        df.to_sql(name=table, con=engine, schema=schema, if_exists='append', dtype=dtype, index=False)
        log.info(f"Upload in die Datenbank erfolgreich abgeschlossen! {len(df)} Datensätze hochgeladen.")
    except Exception as e:
        log.error(f"FEHLER beim Upload: {e}")
        raise