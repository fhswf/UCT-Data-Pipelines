from dotenv import load_dotenv
from utils.landsat_utils import *
from utils.common_utils import setup_logger, get_env_vars

load_dotenv()

# paths + constants
DATA_DIR = r"./data_landsat"
CONFIG_DIR = r"config"
directorys = [DATA_DIR, CONFIG_DIR]

SEARCH_BOUNDING_BOX = "7.87339481,51.51405699,8.17560703,51.62038395"
BOUNDING_BOX_PATH = r"config/bounding_box.json"

TARGET_CRS = '25832'

DW_HOST, DW_PORT, DW_USER, DW_PASSWORD, DB_NAME, ERS_USER, ERS_TOKEN, TABLE_NAME, SCHEMA_NAME = get_env_vars(["DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME", "ERS_USERNAME", "ERS_TOKEN", "TABLE_NAME", "SCHEMA_NAME"])

# Logger initialisieren"
log = setup_logger("logs/landsat.log", "landsat", level=10) # 20 = INFO, 10 = DEBUG
log.debug("Starte Landsat-Skript...")

# Setup überprüfen
setup_api(filepath=DATA_DIR)
check_setup(directorys)

# Verbindung zur Datenbank herstellen
dw_connection = connect_to_dw(DW_USER, DW_PASSWORD, DW_HOST, DW_PORT, DB_NAME)

last_scene_date = get_last_scene_date(dw_connection, quelle="Landsat", table=f"{TABLE_NAME}")
start_date, end_date = get_timerange(last_scene_date)

serviceUrl = "https://m2m.cr.usgs.gov/api/api/json/stable/"

# get login-token
payload = {'username': ERS_USER, 'token': ERS_TOKEN}

apiKey = sendRequest(serviceUrl + "login-token", payload)

datasets = search_dataset(start_date, end_date, apiKey)
log.info(f"{len(datasets)} Datensätze gefunden zwischen {start_date} und {end_date}.")

# download datasets
if datasets: 
    try:
        download = fetch_and_download_scenes(datasets, start_date, end_date, serviceUrl, apiKey)
        api_logout(serviceUrl, apiKey)
        if download == None:
            log.info("Keine neuen Szenen gefunden, skript wird beendet.")
            sys.exit()
        
        # group files
        filegroup = group_files(DATA_DIR)    

        if len(filegroup) == 0:
            log.warning("Keine Dateien zum Gruppieren gefunden.")
        else:
            log.debug("Starte Dateien-Verarbeitung...")
            gdf = process_files(filegroup, dw_connection, target_crs=TARGET_CRS)

            if gdf.empty:
                log.warning("Keine Daten zum Verarbeiten gefunden.")
                raise ValueError("Keine Daten zum Verarbeiten gefunden.")

            formatted_gdf = format_gdf(gdf)
            log.info("Dateien erfolgreich verarbeitet.")

            upload_to_dw(formatted_gdf, dw_connection, SCHEMA_NAME, TABLE_NAME)

    except Exception as e:
        log.error(f"FEHLER: {e}")

    finally:
        clean_up(DATA_DIR)
        log.info("Skript abgeschlossen.")