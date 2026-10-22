import pandas as pd
from sqlalchemy import create_engine
from dotenv import load_dotenv
from utils.dwd_utils import *
from utils.common_utils import setup_logger, get_env_vars

from geoalchemy2 import Geometry
from sqlalchemy.types import String, DateTime, Integer
from sqlalchemy.dialects.postgresql import JSONB


load_dotenv()
logger = setup_logger("logs/dwd.log")

# DWD Stations
STATIONS = ['werl',
            'arnsberg-neheim',
            'lippstadt-bökenförde' 
]

QUELL_IDS = {station: db_quell_id[f"{station}-10"] for station in STATIONS}

# DB connection
HOST, PORT, USER, PASSWORD, DB_NAME, TABLE_NAME, SCHEMA_NAME = get_env_vars(["DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME", "TABLE_NAME", "SCHEMA_NAME"])
ENGINE = create_engine(f"postgresql+psycopg2://{USER}:{PASSWORD}@{HOST}:{PORT}/{DB_NAME}")

# get data from open data server
dataset_urls = construct_urls(dataset_types=["10_minutes"], dataset_codes=dataset_codes, filter_stations=STATIONS)
available_data = download(dataset_urls, logger)

# get latest measurement timestamp from db 
cutoff_dates = {}
latest_timestamps_db = {}
for station in STATIONS:
    qid = db_quell_id[f"{station}-10"]
    select_query = f"SELECT MAX(messzeit_utc) FROM {SCHEMA_NAME}.{TABLE_NAME} WHERE quell_id = '{qid}';"
    latest_timestamp = pd.read_sql(select_query, ENGINE).iloc[0, 0]
    latest_timestamps_db[QUELL_IDS[station]] = latest_timestamp
    cutoff_dates[station] = set_cutoff_dates(latest_timestamp)

# smoothing configuration per station
smooth_config = {
    'smooth_col': "temperature_CH1",
    'last_filter_timestamps': {},
    'filter_states': {}
}

# load last filter states and timestamps from DB
for quell_id in QUELL_IDS.values():
    query = f"""
    SELECT timestamp_utc, properties
    FROM {SCHEMA_NAME}.control
    WHERE control_type = 'filter_state_{quell_id}';
    """

    df_control = pd.read_sql(query, ENGINE)
    if not df_control.empty:
        last_filter_timestamp_db = df_control['timestamp_utc'].iloc[0]
        filter_state_db = np.array(df_control['properties'].iloc[0])
    else:
        last_filter_timestamp_db = None
        filter_state_db = None

    smooth_config['last_filter_timestamps'][quell_id] = last_filter_timestamp_db
    smooth_config['filter_states'][quell_id] = filter_state_db

# load dataframes
df_10_l = load_dfs(available_data, STATIONS, QUELL_IDS, cutoff_dates, logger, ds_type="10_minutes")
df_measures_l = process_df_10(df_10_l)

# apply smoothing to temperature data
df_data_per_station = []
for df in df_measures_l:
    smoothed = apply_smoothing(df, smooth_config, logger)
    df_data_per_station.append(smoothed)

# convert dataframes to db format
dfs_db_import = [df_to_db_format(df) for df in df_data_per_station]

dtype = {
    'messzeit_utc': DateTime,
    'time_range_validity': String,
    'measurement_data': JSONB,
    'quell_id': String,
    'quell_typ': String,
    'quell_name': String,
    'location_id': Integer,
    'location_center': Geometry('POINT', srid=25832),
    'location_area_validity': Geometry('POLYGON', srid=25832),
    'location_id_raster': Integer, 
    'location_area_validity_raster': Geometry('POLYGON', srid=25832),
    'additional_parameters': JSONB,
    'location_properties': JSONB, 
    'location_properties_raster': JSONB,
    'corrected_values': JSONB
}

# DB Import
logger.info(f"Beginne Import in DB.")
for df in dfs_db_import:
    if df is None:
        logger.warning("DataFrame is None, skipping import.")
        continue
    
    try:
        # only keep data after the latest_timestamp_db
        quell_id = df['quell_id'].iloc[0]
        df = df[df['messzeit_utc'] > pd.to_datetime(latest_timestamps_db[quell_id])].reset_index(drop=True)

        logger.info(f"Importiere {len(df)} neue Datensätze für {quell_id} in die DB.")
        
        # dtype only for existing columns
        dtype_filtered = {col: dtype[col] for col in df.columns}

        #df = df.head(1)
        df.to_sql(name=TABLE_NAME, con=ENGINE, schema=SCHEMA_NAME, if_exists='append', dtype=dtype_filtered, index=False)

        # update filter states and timestamps in DB
        timestamp = smooth_config["last_filter_timestamps"][quell_id]
        filter_state = smooth_config["filter_states"][quell_id]
        filter_state_json = json.dumps(filter_state.tolist() if filter_state is not None else None)

        logger.info(f"Aktualisiere Filterzustand für {quell_id} in DB: timestamp={timestamp}, filter_state={filter_state_json}")
        conn = ENGINE.raw_connection()
        cur = conn.cursor()

        sql_update_query = f"""
        UPDATE {SCHEMA_NAME}.control
        SET timestamp_utc = '{timestamp}',
            properties = '{filter_state_json}'
        WHERE control_type = 'filter_state_{quell_id}';
        """

        cur.execute(sql_update_query)
        conn.commit()
        cur.close()
    except Exception as e:
        logger.error(f"Fehler beim Importieren der Daten für {quell_id}: {e}")
        continue

logger.info(f"Import in DB abgeschlossen.")