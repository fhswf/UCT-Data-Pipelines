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

STATIONS = ['werl']

QUELL_IDS = {station: db_quell_id[f"{station}-wr"] for station in STATIONS}
HOST, PORT, USER, PASSWORD, DB_NAME, TABLE_NAME, SCHEMA_NAME = get_env_vars(["DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME", "TABLE_NAME", "SCHEMA_NAME"])
ENGINE = create_engine(f"postgresql+psycopg2://{USER}:{PASSWORD}@{HOST}:{PORT}/{DB_NAME}")

# get data from open data server
dwd_station_id["weather_report"] = {station: dwd_station_id["weather_report"][station] for station in STATIONS}
available_data = download_weather_reports(dwd_station_id["weather_report"], logger)

# get latest timestamp from db to keep only new data 
cutoff_dates = {}
for station in STATIONS:
    qid = db_quell_id[f"{station}-wr"]
    select_query = f"SELECT MAX(messzeit_utc) FROM {SCHEMA_NAME}.{TABLE_NAME} WHERE quell_id = '{qid}';"
    latest_timestamp = pd.read_sql(select_query, ENGINE).iloc[0, 0]
    cutoff_dates[station] = set_cutoff_dates(latest_timestamp)

# load dataframes
df_wr_l = load_dfs(available_data, STATIONS, QUELL_IDS, cutoff_dates, logger, ds_type="weather_report")
df_measures_l = process_df_wr(df_wr_l)

# convert dataframes to db format
df_data_per_station = df_measures_l
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
    'location_properties_raster': JSONB
}

# DB import
logger.info(f"Beginne Import in DB.")
for df in dfs_db_import:
    if df is None:
        logger.warning("DataFrame is None, skipping import.")
        continue

    # dtype only for existing columns
    dtype_filtered = {col: dtype[col] for col in df.columns}

    #df = df.head(1)
    df.to_sql(name=TABLE_NAME, con=ENGINE, schema=SCHEMA_NAME, if_exists='append', dtype=dtype_filtered, index=False)

logger.info(f"Import in DB abgeschlossen.")