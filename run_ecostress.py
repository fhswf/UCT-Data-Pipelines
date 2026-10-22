from dotenv import load_dotenv
from utils.ecostress_utils import *
from utils.common_utils import setup_logger, get_env_vars

load_dotenv()

# paths + constants
DATA_DIR = r"data_ecostress"
CONFIG_DIR = r"config"
directorys = [DATA_DIR, CONFIG_DIR]

SEARCH_BOUNDING_BOX = "7.87339481,51.51405699,8.17560703,51.62038395"
BOUNDING_BOX_PATH = r"config/bounding_box.json"

TARGET_CRS = '25832'

DW_HOST, DW_PORT, DW_USER, DW_PASSWORD, DB_NAME, ECO_USER, ECO_PASSWORD, TABLE_NAME, SCHEMA_NAME = get_env_vars(["DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME", "ECO_USER", "ECO_PASSWORD", "TABLE_NAME", "SCHEMA_NAME"])

# Logger initialisieren"
log = setup_logger("logs/ecostress.log", "ecostress", level=20) # 20 = INFO, 10 = DEBUG
log.debug("Starte Ecostress-Skript...")

# Setup überprüfen
check_setup(directorys)

# API-Token abrufen
token = get_api_token(ECO_USER, ECO_PASSWORD)

# Verbindung zur Datenbank herstellen
dw_connection = connect_to_dw(DW_USER, DW_PASSWORD, DW_HOST, DW_PORT, DB_NAME)

# Letzte verarbeitete Szene abfragen
last_scene_date = get_last_scene_date(dw_connection, table=f"{SCHEMA_NAME}.{TABLE_NAME}")

# Berechne den Zeitbereich für die Abfrage der neuen Szenen
timerange, start_date, end_date = get_timerange(last_scene_date)

if scene_search(timerange, SEARCH_BOUNDING_BOX):
    try:
        task_id = create_request(start_date, end_date, token, BOUNDING_BOX_PATH)
        download_data(task_id, DATA_DIR, token)

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