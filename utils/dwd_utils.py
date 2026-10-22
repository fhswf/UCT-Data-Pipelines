import logging

import geopandas as gpd
import json
import numpy as np
import os
import pandas as pd
import requests
import zipfile

from scipy.signal import cheby1, sosfilt, sosfilt_zi


dwd_station_id = {
    "10_minutes": {
        "arnsberg-neheim": "07330",
        "lippstadt-bökenförde": "03031",
        "werl": "05480",
    },

    "hourly": {
        "arnsberg-neheim": "07330",
        "lippstadt-bökenförde": "03031",
        "werl": "05480",  
    },

    # processed, hourly weather reports
    "weather_report": {
        "lippstadt-bökenförde": "H377_",
        "werl": "10424",
        "soest": "N9350_",
        "sassendorf-beusingsen": "H460_",
    }
}

db_quell_id = {
    # 10 min
    "werl-10": "DWD001",
    "arnsberg-neheim-10": "DWD007",
    "lippstadt-bökenförde-10": "DWD005",

    # weather report
    "werl-wr": "DWD009"
}

dataset_codes = {
    "10_minutes": {
        "10_TU": {
            "quantity": "air_temperature",
            "name": "10minutenwerte_TU",
        },
        "10_TX": {
            "quantity": "extreme_temperature",
            "name": "10minutenwerte_extrema_temp",
        },
        "10_FX": {
            "quantity": "extreme_wind",
            "name": "10minutenwerte_extrema_wind",
        },
        "10_RR": {
            "quantity": "precipitation",
            "name": "10minutenwerte_nieder",
        },
        "10_SD": {
            "quantity": "solar",
            "name": "10minutenwerte_SOLAR",
        },
        "10_FF": {
            "quantity": "wind",
            "name": "10minutenwerte_wind",
        }
    },

    "hourly": {
        "ST_TU": {
            "quantity": "air_temperature",
            "name": "stundenwerte_TU",
        },
        "ST_N": {
            "quantity": "cloudiness",
            "name": "stundenwerte_N",
        },
        "ST_TD": {
            "quantity": "dew_point",
            "name": "stundenwerte_TD",
        },
        "ST_TF": {
            "quantity": "moisture",
            "name": "stundenwerte_TF",
        },
        "ST_P0": {
            "quantity": "pressure",
            "name": "stundenwerte_P0",
        },
        "ST_EB": {
            "quantity": "soil_temperature",
            "name": "stundenwerte_EB",
        },
        "ST_SD": {
            "quantity": "sun",
            "name": "stundenwerte_SD",
        },
        "ST_VV": {
            "quantity": "visibility",
            "name": "stundenwerte_VV",
        },
    }
}


def download(dataset_urls, logger):
    """
    Downloads datasets from the DWD open data server and extracts them to the appropriate directories.
    """

    available_data = {
        "10_minutes": [], 
        "hourly": []
    }

    for code, values in dataset_urls.items():
        station_name = code.split("__")[0]
        url = values["url"]
        ds_type = values["ds_type"]
        quantity = values["quantity"]
        fname = values["fname"]
        directory = f"data_dwd/{station_name}/{ds_type}/{quantity}"

        response = requests.get(url, stream=True)

        if response.status_code != 200:
            logger.info(f"Fehler beim Abruf von {url}: {response.status_code}")
        else:
            # Create directory if it doesn't exist
            if not os.path.exists(directory):
                os.makedirs(directory)

            fp = f"{directory}/{fname}" 
            with open(fp, "wb") as file:
                for chunk in response.iter_content(chunk_size=8192):
                    file.write(chunk)
            
            logger.info(f"Entpacke {fp} nach {directory}")
            with zipfile.ZipFile(fp, 'r') as zip_ref:
                # only extract measurements
                for file in zip_ref.namelist():
                    if file.startswith("produkt"):
                        zip_ref.extract(file, directory)
                        available_data[ds_type].append(f"{directory}/{file}")

            os.remove(fp)
    return available_data

def download_weather_reports(station_ids, logger):
    """
    Downloads weather report datasets from the DWD open data server and saves them to the appropriate directories.
    """

    BASE_URL = "https://opendata.dwd.de/weather/weather_reports/poi/"
    ds_type = "weather_report"

    available_data = {"weather_report": []}
    for station_name, id in station_ids.items():
        
        fname = f"{id}-BEOB.csv"
        url = BASE_URL + fname
        directory = f"data_dwd/{station_name}/{ds_type}"

        response = requests.get(url, stream=True)

        if response.status_code != 200:
            logger.info(f"Fehler beim Abruf von {url}: {response.status_code}")
        else:
            # Create directory if it doesn't exist
            if not os.path.exists(directory):
                os.makedirs(directory)

            logger.info(f"Lade {fname} nach {directory}")
            fp = f"{directory}/{fname}" 
            with open(fp, "wb") as file:
                for chunk in response.iter_content(chunk_size=8192):
                    file.write(chunk)
            
            available_data["weather_report"].append(fp)
    return available_data

def construct_urls(dataset_types, dataset_codes, filter_stations):
    """ 
    Constructs URLs for downloading datasets from the DWD open data server based on the specified dataset types, codes, and stations.
    Args:
        dataset_types (list): List of dataset types to construct URLs for (e.g., ["10_minutes", "hourly"]).
        dataset_codes (dict): Dictionary containing dataset codes and their corresponding quantities and names.
        filter_stations (list): List of station names to filter the constructed URLs.
    Returns:
        dict: A dictionary containing constructed URLs for the specified dataset types and stations.
    """

    DWD_BASE_URL = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate"
    urls = {}

    for ds_type in dataset_types:
        if ds_type == "10_minutes":
            now_or_recent = "now"
        elif ds_type == "hourly":
            now_or_recent = "recent"
        else:
            raise ValueError(f"Unknown dataset type: {ds_type}")

        for station_name, id in dwd_station_id[ds_type].items():
            for code, values in dataset_codes[ds_type].items():
                # convert recent to akt for file name
                if now_or_recent == "recent":
                    transl_recent_str = "akt"
                if now_or_recent == "now": 
                    transl_recent_str = "now"

                urls[f"{station_name}__{code}"] = {
                    "ds_type": ds_type,
                    "quantity": values["quantity"],
                    "name": values["name"],
                    "fname": f"{values['name']}_{id}_{transl_recent_str}.zip",
                    "url": f"{DWD_BASE_URL}/{ds_type}/{values['quantity']}/{now_or_recent}/{values['name']}_{id}_{transl_recent_str}.zip"
                }

    # apply filter for stations
    urls = {key: value for key, value in urls.items() if any(station in key for station in filter_stations)}
    return urls

def set_cutoff_dates(latest_timestamp):
    """
    Sets cutoff dates for different dataset types based on the latest measurement timestamp from the database.
    """

    cutoff = {}
    cutoff["hourly"] = pd.to_datetime("today").normalize() - pd.Timedelta(days=2) # hourly has data only until two days ago
    
    if latest_timestamp is not None:
        cutoff["10_minutes"] = pd.to_datetime(latest_timestamp)
        cutoff["weather_report"] = pd.to_datetime(latest_timestamp)
    else:
        cutoff["10_minutes"] = pd.to_datetime("today").normalize()
        cutoff["weather_report"] = pd.to_datetime("today").normalize()
    return cutoff

def aggregate_dfs(file_paths, station, quell_id, ds_type, logger, cutoff_date=None):
    """
    Aggregates measurement dataframes for a specific station and dataset type by merging them based on the measurement timestamp.
    """

    file_paths_for_station = []
    for fp in file_paths[ds_type]:
        if station in fp:
            file_paths_for_station.append(fp)

    for idx, fp in enumerate(file_paths_for_station):
        logger.info(f"Lade df aus {fp}")
        df = pd.read_csv(fp, sep=";")
        df.columns = df.columns.str.strip()

        # check for specific columns and drop them
        drop_cols = ["eor", "STATIONS_ID"]
        to_drop = [col for col in df.columns if col in drop_cols or col.startswith("QN") or col.startswith("V_N_I")]
        #logger.info(f"Dropped columns: {to_drop}")

        df.drop(columns=to_drop, inplace=True)

        if idx == 0:
            df_merged = df
        else:
            df_merged = pd.merge(df_merged, df, on=["MESS_DATUM"])

    df_merged["quell_id"] = quell_id

    # to datetime
    if ds_type == "10_minutes":
        df_merged["messzeit_UTC"] = pd.to_datetime(df_merged["MESS_DATUM"], format="%Y%m%d%H%M")
        df_merged.drop(columns=["MESS_DATUM"], inplace=True)

    elif ds_type == "hourly":
        df_merged["messzeit_UTC"] = pd.to_datetime(df_merged["MESS_DATUM"], format="%Y%m%d%H")
        df_merged.drop(columns=["MESS_DATUM"], inplace=True)

    elif ds_type == "weather_report":
        df_merged.rename(columns={
            'surface observations': 'Datum',
            'Parameter description': 'Uhrzeit (UTC)',
        }, inplace=True)

        # drop first two rows
        df_merged.drop(index=[0, 1], inplace=True)
        df_merged.reset_index(drop=True, inplace=True)

        df_merged["messzeit_UTC"] = pd.to_datetime(df_merged["Datum"] + " " + df_merged["Uhrzeit (UTC)"], format="%d.%m.%y %H:%M")
        df_merged.drop(columns=["Datum", "Uhrzeit (UTC)"], inplace=True)
    else:
        raise ValueError(f"Unknown dataset type: {ds_type}")
    
    # reorder columns
    df_merged = df_merged[["quell_id", "messzeit_UTC"] + [col for col in df_merged.columns if col not in ["quell_id", "messzeit_UTC"]]]

    # apply cutoff date if provided
    if cutoff_date is not None:
        logger.info(f"Verwende cutoff date: {cutoff_date}")
        df_merged = df_merged[df_merged["messzeit_UTC"] > cutoff_date]

    # sort by messzeit
    df_merged.sort_values(by=["messzeit_UTC"], inplace=True)
    df_merged.reset_index(drop=True, inplace=True)
    return df_merged

def replace_values(df):
    df.replace('---', json.loads('null'), inplace=True)
    df.replace(-999, json.loads('null'), inplace=True)
    df.replace(np.nan, json.loads('null'), inplace=True)
    return df

def load_dfs(data_paths, stations, quell_ids, cutoff_date, logger, ds_type):
    """
    Loads measurement dataframes for specified stations and dataset types, applies necessary transformations, and returns a list of processed dataframes.
    Args:
        data_paths (dict): A dictionary containing the paths to the data files for each dataset type.
        stations (list): A list of station names for which to load data.
        quell_ids (dict): A dictionary mapping station names to their corresponding source IDs.
        cutoff_date (dict): A dictionary containing the cutoff dates for each station and dataset type.
        logger (logging.Logger): The logger instance to use for logging messages.
        ds_type (str): The type of dataset to load (e.g., "10_minutes", "hourly").
    Returns:
        list: A list of processed dataframes.
    """

    # filter stations by available data
    stations = [station for station in stations if any(station in path for path in data_paths[ds_type])]

    df_l = []
    for station in stations:
        quell_id = quell_ids[station]
        df = aggregate_dfs(data_paths, station, quell_id, ds_type, logger, cutoff_date[station][ds_type])

        if ds_type == "10_minutes":
            df = df.rename(columns={
                # air_temperature
                "PP_10": "Luftdruck in Stationshoehe", 
                "TT_10": "Lufttemperatur in 2 m Höhe",
                "TM5_10": "Lufttemperatur in 5 cm Höhe",
                "RF_10": "Relative Feuchte",
                "TD_10": "Taupunkttemperatur",

                # extreme_temperature
                "TX_10": "Maximum der Lufttemperatur in 2 m",
                "TX5_10": "Maximum der Lufttemperatur in 5 cm",
                "TN_10": "Minimum der Lufttemperatur in 2 m",
                "TN5_10": "Minimum der Lufttemperatur in 5 cm",

                # extreme_wind
                "FX_10": "Maximum der Windgeschwindigkeit",
                "DX_10": "Windrichtung des Maximums der Windgeschwindigkeit",
                
                # precipitation
                "RWS_DAU_10": "Niederschlagsdauer",
                "RWS_10": "Niederschlagshöhe",

                # solar
                "DS_10": "Diffuse Himmelstrahlung",
                "GS_10": "Globalstrahlung",
                "SD_10": "Sonnenscheindauer",
                "LS_10": "Langwellige Strahlung",

                # wind
                "FF_10": "Windgeschwindigkeit",
                "DD_10": "Windrichtung",
                "SLA_10": "Stabw. der lateralen Windgeschwindigkeit",
                "SLO_10": "Stabw. der longitudinalen Windgeschwindigkeit"
            })

            to_drop = [
                "FMX_10", # FMX_10 Maximum der Windgeschwindigkeit aus den 1 Minuten Mittelwerten der 3-Sekunden Maxima der vorangegangenen 10 Minuten
                "FNX_10", # Minimum der Windgeschwindigkeit
                "RWS_IND_10", # Niederschlagsindikator, Angabe wie die Messung erfolgte
            ]

        elif ds_type == "hourly":
            df = df.rename(columns={
                # soil_temperature
                "V_TE002": "Erdbodentemperatur in 2 cm Tiefe",
                "V_TE005": "Erdbodentemperatur in 5 cm Tiefe",
                "V_TE010": "Erdbodentemperatur in 10 cm Tiefe",
                "V_TE020": "Erdbodentemperatur in 20 cm Tiefe",
                "V_TE050": "Erdbodentemperatur in 50 cm Tiefe",
                "V_TE100": "Erdbodentemperatur in 100 cm Tiefe",   
            })

            to_drop = [
                "TT_TU", # Lufttemperatur in 2 m Höhe
                "RF_TU", # relative Feuchte
                "TT", # Lufttemperatur in 2 m Hoehe
                "P_STD", # Stundenwerte Luftdruck
                "TT_STD", # Lufttemperatur in 2m Hoehe
                "RF_STD", # Stundenwerte der Relativen Feuchte
                "TD_STD", # Taupunkttemperatur in 2m Hoehe
                "P0", # Luftdruck auf Stationshöhe
                "SD_SO", # stdl. Sonnenscheindauer
                "V_VV_I", # Sichtweiten Index, Angabe wie die Messung erfolgte
                "V_N", # Bewölkungsgrad
                "TD", # Taupunkttemperatur
                "V_VV", # Sichtweite
                "ABSF_STD", # berechnete Stundenwerte der absoluten Feuchte
                "VP_STD", # berechnete Stundenwerte des Dampfdruckes
                "TF_STD", # berechnete Stundenwerte der Feuchttemperatur
                "P", # Luftdruck auf Meereshöhe
            ]

        elif ds_type == "weather_report":
            to_drop = [
                'daily_mean_of_temperature_previous_day',
                'dew_point_temperature_at_2_meter_above_ground',
                'dry_bulb_temperature_at_2_meter_above_ground',
                'direct_solar_radiation_last_24_hours',
                'evaporation/evapotranspiration_last_24_hours',
                'global_radiation_past_24_hours',
                'height_of_base_of_lowest_cloud_above_station',
                'maximum_of_10_minutes_mean_of_wind_speed_for_previous_day',
                'maximum_of_temperature_for_previous_day',
                'maximum_temperature_last_12_hours_2_meters_above_ground',
                'maximum_wind_speed_during_last_6_hours',
                'maximum_wind_speed_for_previous_day',
                'maximum_wind_speed_last_hour',
                'maximum_wind_speed_as_10_minutes_mean_during_last_hour',
                'mean_wind_direction_during_last_10 min_at_10_meters_above_ground',
                'mean_wind_speed_during last_10_min_at_10_meters_above_ground',
                'minimum_of_temperature_at_5_cm_above_ground_for_previous_day',
                'minimum_of_temperature_for_previous_day',
                'minimum_temperature_last_12_hours_2_meters_above_ground',
                'minimum_temperature_last_12_hours_5_cm_above_ground',
                'past_weather_1', 
                'past_weather_2',
                'precipitation_last_12_hours',
                'precipitation_amount_last_24_hours',
                'precipitation_amount_last_3_hours',
                'precipitation_amount_last_6_hours',
                'precipitation_amount_last_hour',
                'pressure_reduced_to_mean_sea_level',
                'relative_humidity',
                'sea/water_temperature',
                'total_time_of_sunshine_past_day',
                'temperature_at_5_cm_above_ground',
            ]

        else:
            raise ValueError(f"Unknown dataset type: {ds_type}")

        df.drop(columns=[col for col in df.columns if col in to_drop], inplace=True)

        df = replace_values(df)
        if ds_type == 'weather_report':
            df['horizontal_visibility'] = df['horizontal_visibility'].replace(",", ".", regex=True).astype(float)
        df = replace_values(df)
        df_l.append(df)
    return df_l

def merge_data_for_stations(df_10_l, df_weather_report_l):
    """
    Merges measurement dataframes for a specific station and dataset type by merging them based on the measurement timestamp.
    """

    df_measures_l = []

    for df_10, df_weather_report in zip(df_10_l, df_weather_report_l):
        data_list = []
        for index, row in df_10.iterrows(): 
            messzeit = row["messzeit_UTC"]
            quell_id = row["quell_id"]

            measures_dict = {
                "air_temperature": {
                    "value": row["Lufttemperatur in 2 m Höhe"] if "Lufttemperatur in 2 m Höhe" in df_10.columns else json.loads('null'),
                    "min": row["Minimum der Lufttemperatur in 2 m"] if "Minimum der Lufttemperatur in 2 m" in df_10.columns else json.loads('null'),
                    "max": row["Maximum der Lufttemperatur in 2 m"] if "Maximum der Lufttemperatur in 2 m" in df_10.columns else json.loads('null')
                },
                "air_temperature_5cm": {
                    "value": row["Lufttemperatur in 5 cm Höhe"] if "Lufttemperatur in 5 cm Höhe" in df_10.columns else json.loads('null'),
                    "min": row["Minimum der Lufttemperatur in 5 cm"] if "Minimum der Lufttemperatur in 5 cm" in df_10.columns else json.loads('null'),
                    "max": row["Maximum der Lufttemperatur in 5 cm"] if "Maximum der Lufttemperatur in 5 cm" in df_10.columns else json.loads('null')
                },
                "dew_point": {
                    "value": row["Taupunkttemperatur"] if "Taupunkttemperatur" in df_10.columns else json.loads('null')
                },
                "diffuse_solar_radiation": {
                    "value": row["Diffuse Himmelstrahlung"] if "Diffuse Himmelstrahlung" in df_10.columns else json.loads('null')
                },
                "global_radiation": {
                    "value": row["Globalstrahlung"] if "Globalstrahlung" in df_10.columns else json.loads('null')
                },
                "humidity": {
                    "value": row["Relative Feuchte"] if "Relative Feuchte" in df_10.columns else json.loads('null')
                },
                "longwave_radiation": {
                    "value": row["Langwellige Strahlung"] if "Langwellige Strahlung" in df_10.columns else json.loads('null')
                },
                "pressure": {
                    "value": row["Luftdruck in Stationshoehe"] if "Luftdruck in Stationshoehe" in df_10.columns else json.loads('null')
                },
                "precipitation": {
                    "value": row["Niederschlagshöhe"] if "Niederschlagshöhe" in df_10.columns else json.loads('null'),
                    "duration": row["Niederschlagsdauer"] if "Niederschlagsdauer" in df_10.columns else json.loads('null')
                },
                "sunshine_duration": {
                    "value": row["Sonnenscheindauer"] if "Sonnenscheindauer" in df_10.columns else json.loads('null')
                },
                "wind_speed": {
                    "value": row["Windgeschwindigkeit"] if "Windgeschwindigkeit" in df_10.columns else json.loads('null'),
                    "max": row["Maximum der Windgeschwindigkeit"] if "Maximum der Windgeschwindigkeit" in df_10.columns else json.loads('null'),
                },
                "wind_direction": {
                    "value": row["Windrichtung"] if "Windrichtung" in df_10.columns else json.loads('null'),
                    "direction_of_max": row["Windrichtung des Maximums der Windgeschwindigkeit"] if "Windrichtung des Maximums der Windgeschwindigkeit" in df_10.columns else json.loads('null')
                }
            }   
            
            measurement_data = json.dumps(measures_dict, separators=(',', ':'))

            data_list.append({
                "messzeit": messzeit,
                "quell_id": quell_id,
                "measurement_data": measurement_data,
                "additional_parameters": json.loads('null')
            })

        # update with data from weather_report
        for entry in data_list:
            messzeit = entry["messzeit"]

            # find the corresponding weather report entry
            weather_report_entry = df_weather_report[df_weather_report["messzeit_UTC"] == messzeit]

            if not weather_report_entry.empty:   
                weather_report_entry = weather_report_entry.iloc[0]

                # add the weather report data to the measurement data
                measures_dict = json.loads(entry["measurement_data"])
                measures_dict.update({
                    "cloudiness": {
                        "value": weather_report_entry["cloud_cover_total"]
                    },
                    "diffuse_solar_radiation_last_hour": {
                        "value": weather_report_entry["diffuse_solar_radiation_last_hour"]
                    },
                    "direct_solar_radiation_last_hour": {
                        "value": weather_report_entry["direct_solar_radiation_last_hour"]
                    },
                    "global_radiation_last_hour": {
                        "value": weather_report_entry["global_radiation_last_hour"]
                    },
                    "horizontal_visibility": {
                        "value": weather_report_entry["horizontal_visibility"]
                    },
                    "weather_code": {
                        "value": weather_report_entry["present_weather"]
                    },
                    "sunshine_duration_last_hour": {
                        "value": weather_report_entry["total_time_of_sunshine_during_last_hour"]
                    },
                    "snow_depth": {
                        "value": weather_report_entry["total_snow_depth"],
                        "new_snow": weather_report_entry["depth_of_new_snow"]
                    }
                })

                # Convert all values in measures_dict to Python native types
                measures_dict = {key: (value if not isinstance(value, dict) else {k: (v.item() if hasattr(v, 'item') else v) for k, v in value.items()}) for key, value in measures_dict.items()}
                entry["measurement_data"] = json.dumps(measures_dict, separators=(',', ':'))

        df_measures = pd.DataFrame(data_list)
        df_measures_l.append(df_measures)
    return df_measures_l

def get_sources(engine):
    """
    Retrieves source information from the database, including location and area validity geometries, and merges them into a single GeoDataFrame.
    """

    source_location = gpd.read_postgis("SELECT * FROM public.quelle", engine, geom_col='location')
    source_area_validity = gpd.read_postgis("SELECT * FROM public.quelle", engine, geom_col='area_validity')
    sources = source_location.merge(source_area_validity, on=['id', 'typ', 'name', 'location_features', 'from_date', 'until_date'])

    # clean up after merge
    sources.drop(columns=["area_validity_x", "location_y", "quell_properties_x"], inplace=True)
    sources.rename(columns={
        "area_validity_y": "area_validity",
        "location_x": "location",
        "quell_properties_y": "quell_properties"
        }, inplace=True)
    return sources

def merge_with_sources(measures, sources):
    """
    Merges the measurement data with source information based on the source ID (quell_id).
    """

    if measures.empty:
        print("Measures DF is empty.")
        return None
    
    # use source data if available
    if measures["quell_id"].unique()[0] not in sources["id"].unique():
        print("Source not found in sources.")
        df_merged = measures.copy()
        df_merged['location'] = json.loads('null')
        df_merged['area_validity'] = json.loads('null')
        df_merged['quell_typ'] = json.loads('null')
        df_merged['quell_name'] = json.loads('null')    
    else:
        df_merged = measures.merge(sources, left_on='quell_id', right_on='id')
        df_merged.drop(columns=["location_features", "id"], inplace=True)
        df_merged.rename(columns={
            "typ": "quell_typ",
            "name": "quell_name",
            }, inplace=True)
    return df_merged

def df_to_db_format(df):
    """
    Converts the merged measurement dataframe into a format suitable for database import, ensuring that all required columns are present and properly formatted.
    """
    
    for col in ['time_range_validity', 'quell_typ', 'quell_name',
                'location_id', 'location_center', 'location_area_validity', 'location_id_raster', 'location_area_validity_raster', 
                'additional_parameters', 'location_properties', 'location_properties_raster', 'corrected_values']:
        if col not in df.columns:
            df[col] = json.loads('null')
    
    # Order columns for database import
    df = df[['messzeit', 'time_range_validity', 'measurement_data', 'quell_id', 'quell_typ', 'quell_name',
             'location_id', 'location_center', 'location_area_validity', 'location_id_raster', 'location_area_validity_raster',
             'additional_parameters', 'location_properties', 'location_properties_raster', 'corrected_values']]
    df = df.sort_values('messzeit')
    df = df.reset_index(drop=True)

    df.rename(columns={"messzeit": "messzeit_utc"}, inplace=True)

    # Convert geometry columns to WKT format
    for col in ['location_center', 'location_area_validity', 'location_area_validity_raster']:
        df[col] = df[col].apply(lambda geom: geom.wkt if geom else json.loads('null'))

    # str to json
    df['measurement_data'] = df['measurement_data'].apply(lambda x: json.loads(x) if isinstance(x, str) else x)
    df['additional_parameters'] = df['additional_parameters'].apply(lambda x: json.loads(x) if isinstance(x, str) else json.loads('null'))
    df['corrected_values'] = df['corrected_values'].apply(lambda x: json.loads(x) if isinstance(x, str) else json.loads('null'))

    # drop empty columns
    df = df.drop(columns=[col for col in df.columns if df[col].isnull().all()])
    return df

def process_df_10(df_list):
    """
    Processes a list of 10-minute measurement dataframes, extracting relevant measurement data and converting it into a structured format suitable for database import.
    """

    df_measures_l = []

    for df_10 in df_list:
        data_list = []
        for index, row in df_10.iterrows(): 
            messzeit = row["messzeit_UTC"]
            quell_id = row["quell_id"]

            measures_dict = {
                "air_temperature": {
                    "value": row["Lufttemperatur in 2 m Höhe"] if "Lufttemperatur in 2 m Höhe" in df_10.columns else json.loads('null'),
                    "min": row["Minimum der Lufttemperatur in 2 m"] if "Minimum der Lufttemperatur in 2 m" in df_10.columns else json.loads('null'),
                    "max": row["Maximum der Lufttemperatur in 2 m"] if "Maximum der Lufttemperatur in 2 m" in df_10.columns else json.loads('null')
                },
                "air_temperature_5cm": {
                    "value": row["Lufttemperatur in 5 cm Höhe"] if "Lufttemperatur in 5 cm Höhe" in df_10.columns else json.loads('null'),
                    "min": row["Minimum der Lufttemperatur in 5 cm"] if "Minimum der Lufttemperatur in 5 cm" in df_10.columns else json.loads('null'),
                    "max": row["Maximum der Lufttemperatur in 5 cm"] if "Maximum der Lufttemperatur in 5 cm" in df_10.columns else json.loads('null')
                },
                "dew_point": {
                    "value": row["Taupunkttemperatur"] if "Taupunkttemperatur" in df_10.columns else json.loads('null')
                },
                "diffuse_solar_radiation": {
                    "value": row["Diffuse Himmelstrahlung"] if "Diffuse Himmelstrahlung" in df_10.columns else json.loads('null')
                },
                "global_radiation": {
                    "value": row["Globalstrahlung"] if "Globalstrahlung" in df_10.columns else json.loads('null')
                },
                "humidity": {
                    "value": row["Relative Feuchte"] if "Relative Feuchte" in df_10.columns else json.loads('null')
                },
                "longwave_radiation": {
                    "value": row["Langwellige Strahlung"] if "Langwellige Strahlung" in df_10.columns else json.loads('null')
                },
                "pressure": {
                    "value": row["Luftdruck in Stationshoehe"] if "Luftdruck in Stationshoehe" in df_10.columns else json.loads('null')
                },
                "precipitation": {
                    "value": row["Niederschlagshöhe"] if "Niederschlagshöhe" in df_10.columns else json.loads('null'),
                    "duration": row["Niederschlagsdauer"] if "Niederschlagsdauer" in df_10.columns else json.loads('null')
                },
                "sunshine_duration": {
                    "value": row["Sonnenscheindauer"] if "Sonnenscheindauer" in df_10.columns else json.loads('null')
                },
                "wind_speed": {
                    "value": row["Windgeschwindigkeit"] if "Windgeschwindigkeit" in df_10.columns else json.loads('null'),
                    "max": row["Maximum der Windgeschwindigkeit"] if "Maximum der Windgeschwindigkeit" in df_10.columns else json.loads('null'),
                },
                "wind_direction": {
                    "value": row["Windrichtung"] if "Windrichtung" in df_10.columns else json.loads('null'),
                    "direction_of_max": row["Windrichtung des Maximums der Windgeschwindigkeit"] if "Windrichtung des Maximums der Windgeschwindigkeit" in df_10.columns else json.loads('null')
                }
            }   
            
            measurement_data = json.dumps(measures_dict, separators=(',', ':'))

            data_list.append({
                "messzeit": messzeit,
                "quell_id": quell_id,
                "measurement_data": measurement_data,
                "additional_parameters": json.loads('null')
            })

        df_measures = pd.DataFrame(data_list)
        df_measures_l.append(df_measures)
    return df_measures_l

def process_df_wr(df_list):
    """
    Processes a list of weather report measurement dataframes, extracting relevant measurement data and converting it into a structured format suitable for database import.
    """

    df_measures_l = []

    for df in df_list:
        data_list = []
        for index, row in df.iterrows(): 
            messzeit = row["messzeit_UTC"]
            quell_id = row["quell_id"]

            measures_dict = {
                "cloudiness": {
                    "value": row["cloud_cover_total"]
                },
                "diffuse_solar_radiation_last_hour": {
                    "value": row["diffuse_solar_radiation_last_hour"]
                },
                "direct_solar_radiation_last_hour": {
                    "value": row["direct_solar_radiation_last_hour"]
                },
                "global_radiation_last_hour": {
                    "value": row["global_radiation_last_hour"]
                },
                "horizontal_visibility": {
                    "value": row["horizontal_visibility"]
                },
                "weather_code": {
                    "value": row["present_weather"]
                },
                "sunshine_duration_last_hour": {
                    "value": row["total_time_of_sunshine_during_last_hour"]
                },
                "snow_depth": {
                    "value": row["total_snow_depth"],
                    "new_snow": row["depth_of_new_snow"]
                }
            }

            # Convert all values in measures_dict to Python native types
            measures_dict = {key: (value if not isinstance(value, dict) else {k: (v.item() if hasattr(v, 'item') else v) for k, v in value.items()}) for key, value in measures_dict.items()}
        
            measurement_data = json.dumps(measures_dict, separators=(',', ':'))

            data_list.append({
                "messzeit": messzeit,
                "quell_id": quell_id,
                "measurement_data": measurement_data,
                "additional_parameters": json.loads('null')
            })

        df_measures = pd.DataFrame(data_list)
        df_measures_l.append(df_measures)
    return df_measures_l

def split_segments(
    df: pd.DataFrame,
    time_col: str = "messzeit_utc",
    value_col: str = "temperature",
    gap_threshold_minutes: int = 30,
    interpolate_small_gaps: bool = True,
    expected_interval_minutes: int = 10
) -> list[pd.DataFrame]:
    """
    Splits a DataFrame into segments based on large gaps in the time column.
    Optionally fills small gaps within the segments using linear interpolation.
    """
    if time_col not in df.columns:
        time_col = "messzeit"

    df = df.sort_values(time_col).reset_index(drop=True).copy()

    # Time diff between consecutive measurements
    df["_gap_minutes"] = df[time_col].diff().dt.total_seconds() / 60

    segments = []
    segment_start = 0

    for i in range(1, len(df)):
        gap = df.loc[i, "_gap_minutes"]

        if gap > gap_threshold_minutes:
            # Bigger gap -> finish segment
            segment = df.iloc[segment_start:i].copy().drop(columns=["_gap_minutes"], errors="ignore")
            if interpolate_small_gaps and not segment.empty:
                segment = _interpolate_gaps(
                    segment,
                    time_col=time_col,
                    value_col=value_col,
                    expected_interval_minutes=expected_interval_minutes
                )
            segments.append(segment)
            segment_start = i

    # Last segment
    segment = df.iloc[segment_start:].copy().drop(columns=["_gap_minutes"], errors="ignore")
    if interpolate_small_gaps and not segment.empty:
        segment = _interpolate_gaps(
            segment,
            time_col=time_col,
            value_col=value_col,
            expected_interval_minutes=expected_interval_minutes
        )
    segments.append(segment)
    return segments


def _interpolate_gaps(
    df: pd.DataFrame,
    time_col: str,
    value_col: str,
    expected_interval_minutes: int
) -> pd.DataFrame:
    """
    Fills small gaps within a segment using linear interpolation.
    Synthetic timestamps are inserted and marked as _interpolated=True.
    """

    df = df.sort_values(time_col).copy()
    df["_interpolated"] = False

    df = df.set_index(time_col)
    full_index = pd.date_range(
        start=df.index.min(),
        end=df.index.max(),
        freq=f"{expected_interval_minutes}min"
    )

    df = df.reindex(full_index)
    df["_interpolated"] = df["_interpolated"].fillna(True)
    df[value_col] = df[value_col].interpolate(method="time")
    df.index.name = time_col
    return df.reset_index()

def chebyshev1_segment(
    df: pd.DataFrame,
    value_col: str = "temperature",
    expected_interval_minutes: int = 10,
    cutoff_period_minutes: float = 90.0,
    order: int = 4,
    ripple_db: float = 0.01,
    logger: logging.Logger = None,
    zi: np.ndarray | None = None, 
) -> tuple: # (df, zi_new)
    """
    Applies a Chebyshev Type I low-pass filter to the specified value column of the DataFrame.
    The filter is applied to each segment of the DataFrame separately, and the filter state can be passed in and returned for continuity across segments.
    Args:
        df: The input DataFrame.
        value_col: The name of the column to be filtered.
        expected_interval_minutes: The expected interval between consecutive measurements in minutes.
        cutoff_period_minutes: The cutoff period in minutes.
        order: The order of the filter.
        ripple_db: The ripple in decibels.
        logger: An optional logger for logging warnings.
        zi: An optional initial filter state.
    Returns:
        A tuple containing the filtered DataFrame and the new filter state.
    """

    y = df[value_col].astype(float).to_numpy()

    dt_sec = expected_interval_minutes * 60.0
    fs = 1.0 / dt_sec
    nyq = 0.5 * fs
    cutoff_hz = 1.0 / (cutoff_period_minutes * 60.0)
    wn = min(max(cutoff_hz / nyq, 1e-6), 0.999999)

    sos = cheby1(order, ripple_db, wn, btype="low", analog=False, output="sos")

    # intitialize state if first call
    if zi is None:
        zi = sosfilt_zi(sos) * y[0]   # scale initial state to first sample

    out_col = f"temperature_CH1"
    try:
        smoothed, zi_new = sosfilt(sos, y, zi=zi) # smoothed is delayed by 3 samples
        df[out_col] = smoothed

    except ValueError as e:
        if logger is not None:
            logger.warning(f"Warning: Filter konnte nicht angewendet werden ({len(y)} samples). Alle Werte in {out_col} werden auf NaN gesetzt. Error: {e}")
        else:
            print(f"Warning: Filter konnte nicht angewendet werden ({len(y)} samples). Alle Werte in {out_col} werden auf NaN gesetzt. Error: {e}")

        df[out_col] = np.nan
        zi_new = None # No new state since the filter could not be applied. Next batch will be re-initialized 

    return df, zi_new

def apply_smoothing(df_measures, smooth_config, logger):
    """
    Applies Chebyshev Type I smoothing to the temperature data in the provided DataFrame.
    The function handles segments of data separately to avoid smoothing over large gaps and maintains filter state across segments.
    """

    smooth_col = smooth_config["smooth_col"]
    last_filter_timestamps = smooth_config["last_filter_timestamps"]
    filter_states = smooth_config["filter_states"]

    quell_id = df_measures["quell_id"].iloc[0]
    last_filter_timestamp = last_filter_timestamps[quell_id]

    # extract temperature from measurement_data
    df_measures['temperature'] = df_measures['measurement_data'].apply(lambda x: json.loads(x).get('air_temperature', {}).get('value', json.loads('null')) if isinstance(x, str) else json.loads('null'))

    # split into segments and apply smoothing to each segment separately to avoid smoothing over large gaps
    segments = split_segments(df_measures)
    segments_smoothed = []
    for segment in segments:
        segment['messzeit'] = pd.to_datetime(segment['messzeit'], utc=True)
        logger.info(f"Segment: {segment['messzeit'].min()} - {segment['messzeit'].max()}")

        # restore filter state if possible
        if last_filter_timestamp is not None: 
            if last_filter_timestamp + pd.Timedelta(minutes=10) in segment["messzeit"].values:
                # continue smoothing from last state
                smoothed = segment[segment["messzeit"] > pd.to_datetime(last_filter_timestamp, utc=True)].copy()
                filter_state = filter_states[quell_id]
            else:
                logger.info(f"Letzter Filterzeitpunkt für {quell_id} kann nicht fortgesetzt werden ({last_filter_timestamp}). Filterzustand wird neu initialisiert.")
                smoothed = segment.copy()
                filter_state = None
        else:
            logger.info(f"Kein letzter Filterzeitpunkt für {quell_id} gefunden. Filterzustand wird neu initialisiert.")
            smoothed = segment.copy()
            filter_state = None
        
        smoothed, filter_state = chebyshev1_segment(df=smoothed, logger=logger, zi=filter_state)

        # save last_timestamp and filter_state
        if not np.isnan(smoothed[smooth_col].iloc[-1]):
            filter_states[quell_id] = filter_state
            last_filter_timestamps[quell_id] = smoothed['messzeit'].max()
        else:
            logger.warning("Warning: Glättung fehlgeschlagen. Filter-Zustand wird resettet.")
            filter_states[quell_id] = None
            last_filter_timestamps[quell_id] = None

        # shift timestamp by 30 minutes (3 samples delay)
        smoothed['messzeit'] = smoothed['messzeit'] - pd.Timedelta(minutes=30)

        # align smoothed values with original segment by timestamp
        timestamps_segment = segment['messzeit']
        timestamps_smoothed = smoothed['messzeit']
        timestamps_intersection = timestamps_segment[timestamps_segment.isin(timestamps_smoothed)]

        mask_segment = segment['messzeit'].isin(timestamps_intersection)
        mask_smoothed = smoothed['messzeit'].isin(timestamps_intersection)
        assert mask_segment.sum() == mask_smoothed.sum(), "Anzahl übereinstimmender Zeitstempel muss gleich sein"

        segment.loc[mask_segment, smooth_col] = smoothed.loc[mask_smoothed, smooth_col].values

        # drop last 3 rows
        segment = segment[:-3].copy()
        segments_smoothed.append(segment)

        # reset filter state for next segment
        filter_state = None

    df = pd.concat(segments).sort_values("messzeit").reset_index(drop=True)
    df['_interpolated'] = df['_interpolated'].fillna(False)

    num_interpolated = df['_interpolated'].sum()
    logger.info(f"Anzahl interpolierter Werte: {num_interpolated}")

    # add smoothed temperature as new column
    df_measures["temperature_smoothed"] = df[smooth_col]
    df_measures = df_measures[:-3].copy()

    # to corrected_values json
    def corrected_values_json(row):
        json_dict = {
            "temperature": {
                "main": {
                    "value": row["temperature_smoothed"] if not pd.isna(row["temperature_smoothed"]) else row["temperature"],
                    "stage": "smoothed" if not pd.isna(row["temperature_smoothed"]) else "raw"
                }
            }
        }
        return json_dict
    
    df_measures["corrected_values"] = df_measures.apply(corrected_values_json, axis=1)
    df_measures["corrected_values"] = df_measures["corrected_values"].apply(lambda x: json.dumps(x))
    df_measures['corrected_values'] = df_measures['corrected_values'].str.replace('NaN', 'null')

    df_measures.drop(columns=["temperature", "temperature_smoothed"], inplace=True)
    return df_measures