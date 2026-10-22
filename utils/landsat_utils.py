import json
import requests
import sys
import time
import datetime
import threading
import re
from dotenv import load_dotenv
import os
import pandas as pd
import logging
from datetime import datetime, timedelta
import numpy as np
import geopandas as gpd
from shapely.geometry import box
import rasterio
from rasterio import features
import sqlalchemy
from sqlalchemy import create_engine, text
from sqlalchemy.types import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
import geoalchemy2
from geoalchemy2.types import Geometry
import xml.etree.ElementTree as ET

from utils.ecostress_utils import spatial_join_with_quelle

serviceUrl = "https://m2m.cr.usgs.gov/api/api/json/stable/"
# Filter und Variablen
# Set dataset for Landsat 8-9
datasetName = "landsat_ot_c2_l2"
 # Spatial filter for the specified coordinates (creating a small bounding box)
center_lat = 51.5764  # EPSG:4326
center_lon = 7.8878   # EPSG:4326
delta = 0

spatialFilter = {'filterType': "mbr",
                    'lowerLeft': {'latitude': center_lat - delta, 'longitude': center_lon - delta},
                    'upperRight': {'latitude': center_lat + delta, 'longitude': center_lon + delta}}

cloudCoverCriteria = {
            "min": 0,
            "max": 66,
            "includeUnknown": False
        }    

additionalCriteria = {
            "filterType": "value",
            "fieldId": "5e83d0b84df8d8f2f2c30fec",  # Cloud cover field ID
            "values": ["0", "66"],
            "operand": "between"
        }

#region setup
# logger initialisieren
log = logging.getLogger("landsat")

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
#endregion

#region Database
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
        WHERE q.typ = 'Landsat'
          AND l.from_date <= NOW()
          AND l.until_date >= NOW()
    """

    quelle = gpd.read_postgis(query, dw_connection, geom_col='area_validity')
    log.info("Quelle erfolgreich geladen.")
    return quelle
#endregion

#region API

def setup_api(filepath : str = r"../data_landsat", maxthreads : int = 5) -> tuple:
    """
    Setzt die API-Parameter und initialisiert die globalen Variablen.
    Args:
        filepath (str): Pfad zum Verzeichnis, in dem die heruntergeladenen Dateien gespeichert werden sollen.
        maxthreads (int): Maximale Anzahl gleichzeitiger Threads für den Download.
    Returns:
        tuple: Ein Tupel mit den folgenden Werten:
            - path (str): Der Pfad zum Verzeichnis.
            - maxthreads (int): Maximale Anzahl gleichzeitiger Threads.
            - sema (threading.Semaphore): Semaphore-Objekt für die Thread-Synchronisation.
            - label (str): Ein Zeitstempel-Label für die Downloads.
            - threads (list): Liste der Threads für den Download.
    """
    #global variables
    global path, sema, label, threads
    
    path = filepath
    sema = threading.Semaphore(value=maxthreads)
    label = datetime.now().strftime("%Y%m%d_%H%M%S")
    threads = []

    return path, maxthreads, sema, label, threads

# send http request
def sendRequest(url : str, data : dict, apiKey : str = None) -> dict:
    """
    Sendet eine HTTP-Anfrage an die angegebene URL mit den angegebenen Daten und dem API-Schlüssel.
    Args:
        url (str): Die URL, an die die Anfrage gesendet werden soll.
        data (dict): Die Daten, die in der Anfrage gesendet werden sollen.
        apiKey (str, optional): Der API-Schlüssel für die Authentifizierung. Standardmäßig None.
    Returns:
        dict: Die Antwort der API als Dictionary.
    """
    pos = url.rfind('/') + 1
    endpoint = url[pos:]
    json_data = json.dumps(data)
    
    if apiKey == None:
        response = requests.post(url, json_data)
    else:
        headers = {'X-Auth-Token': apiKey}              
        response = requests.post(url, json_data, headers = headers)    
    
    try:
        httpStatusCode = response.status_code 
        if response == None:
            log.error("No output from service")
            sys.exit()
        output = json.loads(response.text)	
        if output['errorCode'] != None:
            log.error("Failed Request ID", output['requestId'])
            log.error(output['errorCode'], "-", output['errorMessage'])
            sys.exit()
        if  httpStatusCode == 404:
            log.error("404 Not Found")
            sys.exit()
        elif httpStatusCode == 401: 
            log.error("401 Unauthorized")
            sys.exit()
        elif httpStatusCode == 400:
            log.error("Error Code", httpStatusCode)
            sys.exit()
    except Exception as e: 
        response.close()
        pos=serviceUrl.find('api')
        log.error(f"Failed to parse request {endpoint} response. Re-check the input {json_data}. The input examples can be found at {url[:pos]}api/docs/reference/#{endpoint}")
        sys.exit()
    response.close()    
    log.debug(f"Finished request {endpoint} with request ID {output['requestId']}")
    
    return output['data']

def downloadFile(url : str) -> None:
    """
    Lädt eine Datei von der angegebenen URL herunter und speichert sie im angegebenen Verzeichnis.
    Args:
        url (str): Die URL, von der die Datei heruntergeladen werden soll.
    """
    sema.acquire()
    try:        
        response = requests.get(url, stream=True)
        disposition = response.headers['content-disposition']
        filename = re.findall("filename=(.+)", disposition)[0].strip("\"")
        log.debug(f"Downloading {filename} ...")
        if path != "" and path[-1] != "/":
            filename = "/" + filename
        os.makedirs(path, exist_ok=True)
        open(path + filename, 'wb').write(response.content)
        log.debug(f"Downloaded {filename}")
        sema.release()
    except Exception as e:
        log.warning(f"Failed to download from {url}. {e}. Will try to re-download.")
        sema.release()
        runDownload(threads, url)
    
def runDownload(threads: list, url : str) -> None:
    """
    Startet den Download in einem neuen Thread und fügt ihn der Liste der Threads hinzu.
    Args:
        threads (list): Liste der Threads für den Download.
        url (str): Die URL, von der die Datei heruntergeladen werden soll.
    """
    thread = threading.Thread(target=downloadFile, args=(url,))
    threads.append(thread)
    thread.start()

def get_timerange(start_time : datetime) -> tuple[str, str]:
    """
    Berechnet den Zeitraum für die Abfrage neuer Szenen und bringt die Zeitstempel in das richtige Format.
    Auf das Startdatum wird ein Tag addiert, um nach neuen Szenen zu suchen und Duplikate zu vermeiden.

    Args:
        start_time (datetime): Startdatum der Anfrage

    Returns:
        tuple: Tupel mit zwei Zeitstempeln:
            - str: Startdatum   | Format: "MM-DD-YYYY".
            - str: Enddatum     | Format: "MM-DD-YYYY".
    """
    end_time = datetime.now().replace(second=0, microsecond=0)

    start_time = start_time + timedelta(days=1) # search for new scenes
    start_time_formatted = start_time.strftime("%Y-%m-%d")
    end_time_formatted = end_time.strftime("%Y-%m-%d")

    return start_time_formatted, end_time_formatted

def search_dataset(start_date : str, end_date : str, apiKey : str) -> dict:
    """
    Sucht nach Datensätzen in der API mit den angegebenen Zeitstempeln und dem Dataset-Namen.
    Args:
        start_date (str): Startdatum der Anfrage | Format: "MM-DD-YYYY".
        end_date (str): Enddatum der Anfrage | Format: "MM-DD-YYYY".
        apiKey (str): API-Schlüssel für die Authentifizierung.
    Returns:
        dict: Die gefundenen Datensätze als Dictionary.
    """
    # Temporal filter from user input
    temporalFilter = {'start': start_date, 'end': end_date}

    payload = {'catalogId': "EE",
                'datasetName': datasetName,
                'spatialFilter': spatialFilter,
                'temporalFilter': temporalFilter}                     

    log.info("Searching datasets...")
    datasets = sendRequest(serviceUrl + "dataset-search", payload, apiKey)

    if datasets != None:
        log.debug(f"Found {len(datasets)} datasets")
    else:
        log.debug("No datasets found")

    return datasets

def fetch_and_download_scenes(datasets : list, start_date : str, end_date : str, serviceUrl : str, apiKey : str) -> int:
    """
    Sucht nach Szenen in den angegebenen Datensätzen und lädt sie herunter.
    Args:
        datasets (list): Liste der Datensätze, in denen nach Szenen gesucht werden soll.
        start_date (str): Startdatum der Anfrage | Format: "MM-DD-YYYY".
        end_date (str): Enddatum der Anfrage | Format: "MM-DD-YYYY".
        serviceUrl (str): URL des API-Services.
        apiKey (str): API-Schlüssel für die Authentifizierung.
    Returns:
        int: Anzahl der heruntergeladenen Dateien oder None, wenn keine neuen Szenen gefunden wurden.
    """
    for dataset in datasets:
        acquisitionFilter = {"end": end_date, "start": start_date}
        
        payload = {'datasetName': dataset['datasetAlias'], 
                   'maxResults': 100,
                   'startingNumber': 1, 
                   'sceneFilter': {
                       'spatialFilter': spatialFilter,
                       'acquisitionFilter': acquisitionFilter,
                       'cloudCoverFilter': cloudCoverCriteria
                   }}
        
        log.info("Searching scenes...")   
        
        scenes = sendRequest(serviceUrl + "scene-search", payload, apiKey)

        log.info(f"Found {scenes['recordsReturned']} scenes")

        if scenes['recordsReturned'] > 0:
            sceneIds = []
            for result in scenes['results']:
                sceneIds.append(result['entityId'])

            sceneIds_string = ', '.join(sceneIds).strip("'")
            
            payload = {'datasetName': dataset['datasetAlias'], 'entityIds': sceneIds}

            downloadOptions = sendRequest(serviceUrl + "download-options", payload, apiKey)
        
            downloads = []
            for paket in downloadOptions:
                if paket['downloadSystem'] == "folder":
                    for product in paket['secondaryDownloads']:
                    # Only download the specified bands
                        if product['available'] == True:
                            if "L2ST" in product['entityId'] or product['entityId'].endswith("QA_PIXEL_TIF"):
                                if (product['productName'].endswith("ST_B10.TIF") or product['displayId'].endswith("ST_B10.TIF") or
                                    product['productName'].endswith("QA_PIXEL.TIF") or product['displayId'].endswith("QA_PIXEL.TIF") or
                                    product['productName'].endswith("MTL.xml") or product['displayId'].endswith("MTL.xml")):
                                    downloads.append({'entityId': product['entityId'],
                                                    'productId': product['id']})

            if downloads:
                requestedDownloadsCount = len(downloads)
                label = datetime.now().strftime("%Y%m%d_%H%M%S")
                payload = {'downloads': downloads, 'label': label}
                requestResults = sendRequest(serviceUrl + "download-request", payload, apiKey)          
                              
                if requestResults['preparingDownloads'] != None and len(requestResults['preparingDownloads']) > 0:
                    payload = {'label': label}
                    moreDownloadUrls = sendRequest(serviceUrl + "download-retrieve", payload, apiKey)
                    
                    downloadIds = []  
                    
                    for download in moreDownloadUrls['available']:
                        if str(download['downloadId']) in requestResults['newRecords'] or str(download['downloadId']) in requestResults['duplicateProducts']:
                            downloadIds.append(download['downloadId'])
                            runDownload(threads, download['url'])
                        
                    for download in moreDownloadUrls['requested']:
                        if str(download['downloadId']) in requestResults['newRecords'] or str(download['downloadId']) in requestResults['duplicateProducts']:
                            downloadIds.append(download['downloadId'])
                            runDownload(threads, download['url'])
                     
                    while len(downloadIds) < (requestedDownloadsCount - len(requestResults['failed'])): 
                        preparingDownloads = requestedDownloadsCount - len(downloadIds) - len(requestResults['failed'])
                        log.info(f"{preparingDownloads}: downloads are not available. Waiting for 30 seconds.")
                        time.sleep(30)
                        log.info("Trying to retrieve data")
                        moreDownloadUrls = sendRequest(serviceUrl + "download-retrieve", payload, apiKey)
                        for download in moreDownloadUrls['available']:                            
                            if download['downloadId'] not in downloadIds and (str(download['downloadId']) in requestResults['newRecords'] or str(download['downloadId']) in requestResults['duplicateProducts']):
                                downloadIds.append(download['downloadId'])
                                runDownload(threads, download['url'])
                            
                else:
                    for download in requestResults['availableDownloads']:
                        runDownload(threads, download['url'])
        else:
            log.info("Search found no results.")
            return
    log.info(f"Downloading {len(download)} files...")
    log.info("Downloading files... Please do not close the program")
    for thread in threads:
        thread.join()
            
    log.info("Complete Downloading")
    return len(downloads)

def api_logout(serviceUrl : str, apiKey : str) -> None:
    """
    Meldet den Benutzer von der API ab.
    Args:
        serviceUrl (str): URL des API-Services.
        apiKey (str): API-Schlüssel für die Authentifizierung.
    """
    if sendRequest(serviceUrl + "logout", None, apiKey) == None:        
        log.info("Logged Out")
    else:
        log.warning("Logout Failed")

#endregion

#region file organization
def get_file_parts(file_name : str) -> list[str]:
    """Extrahiert die ersten 7 Teile des Dateinamens."""
    return file_name.split("_")[:7]

def get_file_suffix(file_name : str) -> str:
    """Extrahiert den Suffix der Datei (z. B. 'MTL.xml', 'QA_PIXEL.TIF')."""
    return file_name.split("_")[-1]

def group_files_by_prefix(directory : str) -> dict:
    """Gruppiert Dateien nach den ersten 7 Teilen des Dateinamens."""
    groups = {}

    for file in os.listdir(directory):
        if not os.path.isfile(os.path.join(directory, file)):
            continue  # Überspringen, falls es ein Verzeichnis ist

        parts = get_file_parts(file)
        group_key = "_".join(parts)

        groups.setdefault(group_key, set()).add(file)

    return groups

def filter_valid_groups(groups : dict, required_suffixes : set) -> dict:
    """Filtert Gruppen, die nicht alle erforderlichen Dateitypen enthalten."""
    valid_groups = {}

    for key, files in groups.items():
        # Ermitteln der Dateitypen in der aktuellen Gruppe
        file_suffixes = {get_file_suffix(f) for f in files}

        if required_suffixes.issubset(file_suffixes):
            valid_groups[key] = list(files)
        else:
            log.warning(f"Gruppe {key} wurde rausgefiltert, weil folgende Dateitypen fehlen: "
                  f"{required_suffixes - file_suffixes}")
    return valid_groups

def group_files(directory : str, required_suffixes : set ={"MTL.xml", "PIXEL.TIF", "B10.TIF"}) -> dict:
    """
    Gruppiert Dateien im angegebenen Verzeichnis nach den ersten 7 Teilen des Dateinamens und filtert Gruppen,
    die nicht alle erforderlichen Dateitypen enthalten.
    Args:
        directory (str): Verzeichnis, in dem die Dateien gruppiert werden sollen.
        required_suffixes (set): Menge der erforderlichen Dateitypen, die in jeder Gruppe vorhanden sein müssen.
    Returns:
        dict: Ein Dictionary, das die Gruppen von Dateien enthält, die alle erforderlichen Dateitypen haben.
    """
    groups = group_files_by_prefix(directory)
    valid_groups = filter_valid_groups(groups, required_suffixes)

    log.info(f"{len(valid_groups)} Gruppen gebildet.")
    return valid_groups

def organize_files(files : list[str]) -> tuple[str, str, str]:
    """
    Organisiert die Dateien in der Gruppe und gibt sie in der richtigen Reihenfolge zurück.
    """
    lst_file = next((f for f in files if f.endswith("B10.TIF")), None)
    qa_file = next((f for f in files if f.endswith("QA_PIXEL.TIF")), None)
    mtl_file = next((f for f in files if f.endswith("MTL.xml")), None)

    if lst_file is None or qa_file is None or mtl_file is None:
        log.error("Fehlende erforderliche Dateien in der Gruppe")
        raise ValueError("Fehlende erforderliche Dateien in der Gruppe")
    
    # Vollständige Pfade erstellen
    mtl_file = os.path.join(path, mtl_file)
    lst_file = os.path.join(path, lst_file)
    qa_file = os.path.join(path, qa_file)

    return mtl_file, lst_file, qa_file

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
#endregion

#region processing

def get_scene_datetime(scene_name : str, mtl_file : str) -> datetime:
    """
    Extrahiert das Datum und die Uhrzeit der Szene aus dem MTL-XML-Dokument.
    Args:
        scene_name (str): Name der Szene.
        mtl_file (str): Pfad zur MTL-XML-Datei.
    Returns:
        datetime: Datum und Uhrzeit der Szene.
    """
    try:
        tree = ET.parse(mtl_file)
    except ET.ParseError as e:
        log.error(f"FEHLER: Fehler beim Parsen der XML-Datei {mtl_file}: {e}")
        raise ValueError(f"Ungültige XML-Datei: {mtl_file}")
    root = tree.getroot()

    scene_center_time = root.find(".//SCENE_CENTER_TIME")
    if scene_center_time is None:
        log.warning(f"SCENE_CENTER_TIME not found in {mtl_file}")
        raise ValueError("SCENE_CENTER_TIME not found in MTL file")
    else:
        date_str = scene_name.split("_")[3]
        
        datetime_str = f"{date_str}T{scene_center_time.text.strip()[:15]}"
        return datetime.strptime(datetime_str, "%Y%m%dT%H:%M:%S.%f")
    
def get_valid_mask(qa_file : str) -> np.ndarray:
    """
    Erstellt eine Maske für gültige Pixel aus der QA-Pixel-Datei. Bewölkte Pixel und Pixel außerhalb des Shapefiles werden ausgeschlossen.
    Args:
        qa_file (str): Pfad zur QA-Pixel-Datei.
    Returns:
        np.ndarray: Maske für gültige Pixel.
    """
    # only valid and clear pixels, where QA_Pixel == 21824
    with rasterio.open(qa_file) as src:
        qa_data = src.read(1)
        valid_mask = (qa_data == 21824)

        # and only pixels, that are in the shape of the shapefile
        shapefile = gpd.read_file(r"./config/bounding_box_soest/bounding_box.shp")
        shapes = [geometry for geometry in shapefile.geometry if geometry.is_valid]
        bounds_geometry = box(*src.bounds)  # Convert bounds to a Geometry object

        valid_mask = features.geometry_mask(
            shapes,
            transform=src.transform,
            invert=True,
            out_shape=src.shape
        ) & valid_mask

        return valid_mask
    
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
                if valid_mask is not None and not bool(valid_mask[row, col]):
                    continue
                value = image[row, col]
                if not np.isnan(value):
                    minx, miny = transform * (col, row)
                    maxx, maxy = transform * (col + 1, row + 1)
                    geom = box(minx, miny, maxx, maxy)
                    data.append((geom, value_func(value)))
    return data

def process_scene(lst_file : str, qa_file : str, scene_datetime : datetime) -> tuple:
    """
    Verarbeitet eine Szene, indem sie die LST-Daten und die QA-Pixel-Daten extrahiert und in Polygone umwandelt.
    Args:
        lst_file (str): Pfad zur LST-Datei.
        qa_file (str): Pfad zur QA-Pixel-Datei.
        scene_datetime (datetime): Datum und Uhrzeit der Szene.
    Returns:
        tuple: Ein Tupel mit den folgenden Werten:
            - polygons (list): Liste der Polygone.
            - values (list): Liste der Werte.
            - timestamps (list): Liste der Zeitstempel.
            - crs: Koordinatenreferenzsystem.
    """
    with rasterio.open(lst_file) as src:
        transform = src.transform
        crs = src.crs

    valid_mask = get_valid_mask(qa_file)

    temperature_data = extract_valid_pixels(lst_file, transform, lambda v: {"temperature": {"value": json.loads(str((v * 0.00341802 + 149.0) - 273.15))}}, valid_mask)
   
    polygons = [geom for geom, _ in temperature_data]
    values = [val for _, val in temperature_data]
    timestamps = [scene_datetime] * len(polygons)

    return polygons, values, timestamps, crs

def create_geodataframe(polygons : list, values : list, timestamps : list, crs) -> gpd.GeoDataFrame:
    """
    Erstellt ein GeoDataFrame aus den gegebenen Polygonen, Werten und Zeitstempeln.
    Args:
        polygons (list): Liste der Polygone.
        values (list): Liste der Werte.
        timestamps (list): Liste der Zeitstempel.
        crs: Koordinatenreferenzsystem.
    Returns:
        gpd.GeoDataFrame: GeoDataFrame mit den gegebenen Daten.
    """
    gdf = gpd.GeoDataFrame({
        'geometry': polygons,
        'value': values,
        'timestamp': timestamps,
    }, crs=crs, geometry='geometry')
    return gdf

def process_files(filegroup : dict, dw_connection : sqlalchemy.engine.base.Engine, target_crs : str ='25832') -> gpd.GeoDataFrame:
    """
    Verarbeitet die Dateien in der angegebenen Dateigruppe und erstellt ein GeoDataFrame.
    Args:
        filegroup (dict): Dictionary mit Dateigruppen.
        dw_connection (sqlalchemy.engine.base.Engine): SQLAlchemy-Engine für die Datenbankverbindung.
        target_crs (str): Ziel-CRS für das GeoDataFrame. Standard: '25832'.
    Returns:
        gpd.GeoDataFrame: GeoDataFrame mit den verarbeiteten Daten.
    """
    all_polygons, all_values, all_timestamps = [], [], []
    log.info(f"Starte Verarbeitung von {len(filegroup)} Dateigruppen...")

    count = 1
    for scene_name, files in filegroup.items():
        log.debug(f"Verarbeite Dateigruppe: {count} / {len(filegroup)}")
        count += 1

        mtl_file, lst_file, qa_file = organize_files(files)

        scene_datetime = get_scene_datetime(scene_name, mtl_file)
        if scene_datetime is None:
            log.warning(f"Ungültiges Datum in der MTL-Datei: {mtl_file}")
            continue
        
        polygons, values, timestamps, crs = process_scene(lst_file, qa_file, scene_datetime)

        all_polygons.extend(polygons)
        all_values.extend(values)
        all_timestamps.extend(timestamps)

    # Erstellen eines GeoDataFrames
    gdf = create_geodataframe(all_polygons, all_values, all_timestamps, crs)
    gdf = gdf.to_crs(epsg=target_crs)

    quelle = get_quelle(dw_connection)
    gdf = spatial_join_with_quelle(gdf, quelle)

    return gdf
#endregion
#region formatting and upload

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
            gpd.GeoDataFrame: Das formatierte GeoDataFrame. | Format: ['messzeit_utc', 'time_range_validity', 'measurement_data', 'quell_id', 'quell_typ', 'quell_name', 'location', 'area_validity', 'additional_parameters']
        """
        log.info("Starte Formatierung des GeoDataFrames...")

        df = pd.DataFrame(gdf).copy()
        df['messzeit_utc'] = df['timestamp']
        df['quell_id'] = df['id']
        df['measurement_data'] = df['value']
        df['additional_parameters'] = None

        return df[['messzeit_utc', 'quell_id', 'measurement_data', 'additional_parameters']]

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
        log.info(f"Upload erfolgreich: {len(df)} Zeilen übertragen.")
    except Exception as e:
        log.error(f"FEHLER beim Upload: {e}")
        raise