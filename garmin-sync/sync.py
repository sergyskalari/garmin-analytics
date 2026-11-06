"""Recurring sync: activities (+ laps) and daily wellness from Garmin Connect
into SQLite. Designed to run in a loop (see docker-compose.yml); it needs no
manual intervention once the authentication tokens exist in
/app/data/tokens.

Requires: garminconnect==0.3.16, tzdata (see docker-compose.yml)
"""

from garminconnect import Garmin
import sqlite3
import json
import time
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

MADRID_TZ = ZoneInfo("Europe/Madrid")
TOKENSTORE = '/app/data/tokens'
DB_PATH = '/app/data/garmin.db'
PAUSA_ENTRE_LLAMADAS = 1.5

SCHEMA_ACTIVITIES = """
CREATE TABLE IF NOT EXISTS activities (
    activity_id INTEGER PRIMARY KEY,
    activity_name TEXT,
    description TEXT,
    activity_type TEXT,
    start_time_local TEXT,
    start_time_gmt TEXT,
    location_name TEXT,
    distance_m REAL,
    duration_s REAL,
    moving_duration_s REAL,
    elevation_gain_m REAL,
    elevation_loss_m REAL,
    average_speed_mps REAL,
    max_speed_mps REAL,
    calories REAL,
    average_hr REAL,
    max_hr REAL,
    average_cadence REAL,
    max_cadence REAL,
    steps INTEGER,
    avg_power REAL,
    max_power REAL,
    norm_power REAL,
    aerobic_training_effect REAL,
    anaerobic_training_effect REAL,
    training_effect_label TEXT,
    activity_training_load REAL,
    vo2max_value REAL,
    avg_vertical_oscillation REAL,
    avg_ground_contact_time REAL,
    avg_stride_length REAL,
    avg_vertical_ratio REAL,
    hr_zone_1 REAL,
    hr_zone_2 REAL,
    hr_zone_3 REAL,
    hr_zone_4 REAL,
    hr_zone_5 REAL,
    lap_count INTEGER,
    device_id INTEGER,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS activity_laps (
    activity_id INTEGER,
    lap_index INTEGER,
    start_time_gmt TEXT,
    distance_m REAL,
    duration_s REAL,
    moving_duration_s REAL,
    elevation_gain_m REAL,
    elevation_loss_m REAL,
    average_speed_mps REAL,
    max_speed_mps REAL,
    calories REAL,
    average_hr REAL,
    max_hr REAL,
    average_cadence REAL,
    max_cadence REAL,
    average_power REAL,
    max_power REAL,
    normalized_power REAL,
    ground_contact_time REAL,
    stride_length REAL,
    vertical_oscillation REAL,
    vertical_ratio REAL,
    avg_grade_adjusted_speed REAL,
    intensity_type TEXT,
    raw_json TEXT,
    PRIMARY KEY (activity_id, lap_index)
);
"""

SCHEMA_WELLNESS = """
CREATE TABLE IF NOT EXISTS daily_wellness (
    calendar_date TEXT PRIMARY KEY,
    sleep_time_s INTEGER, nap_time_s INTEGER,
    deep_sleep_s INTEGER, light_sleep_s INTEGER, rem_sleep_s INTEGER, awake_sleep_s INTEGER,
    awake_count INTEGER,
    avg_spo2 REAL, lowest_spo2 REAL, highest_spo2 REAL,
    avg_respiration REAL, lowest_respiration REAL, highest_respiration REAL,
    avg_sleep_stress REAL, avg_sleep_hr REAL,
    sleep_score INTEGER, sleep_score_qualifier TEXT,
    sleep_rem_pct INTEGER, sleep_light_pct INTEGER, sleep_deep_pct INTEGER,
    sleep_start_local TEXT, sleep_end_local TEXT,
    avg_stress_level INTEGER, max_stress_level INTEGER,
    hrv_weekly_avg INTEGER, hrv_last_night_avg INTEGER, hrv_last_night_5min_high INTEGER,
    hrv_status TEXT, hrv_balanced_low INTEGER, hrv_balanced_upper INTEGER,
    resting_hr INTEGER,
    raw_sleep TEXT, raw_stress TEXT, raw_hrv TEXT, raw_rhr TEXT
);
"""


def epoch_local_a_texto(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=None).strftime('%Y-%m-%d %H:%M:%S')


def extraer_fila_wellness(fecha_str, sleep, stress, hrv, rhr):
    dto = (sleep or {}).get('dailySleepDTO') or {}
    scores = dto.get('sleepScores') or {}
    overall = scores.get('overall') or {}
    rem = scores.get('remPercentage') or {}
    light = scores.get('lightPercentage') or {}
    deep = scores.get('deepPercentage') or {}
    hrv_sum = (hrv or {}).get('hrvSummary') or {}
    baseline = hrv_sum.get('baseline') or {}
    rhr_lista = (((rhr or {}).get('allMetrics') or {}).get('metricsMap') or {}).get(
        'WELLNESS_RESTING_HEART_RATE') or []
    resting_hr = rhr_lista[0].get('value') if rhr_lista else None

    return (
        fecha_str,
        dto.get('sleepTimeSeconds'), dto.get('napTimeSeconds'),
        dto.get('deepSleepSeconds'), dto.get('lightSleepSeconds'),
        dto.get('remSleepSeconds'), dto.get('awakeSleepSeconds'),
        dto.get('awakeCount'),
        dto.get('averageSpO2Value'), dto.get('lowestSpO2Value'), dto.get('highestSpO2Value'),
        dto.get('averageRespirationValue'), dto.get('lowestRespirationValue'), dto.get('highestRespirationValue'),
        dto.get('avgSleepStress'), dto.get('avgHeartRate'),
        overall.get('value'), overall.get('qualifierKey'),
        rem.get('value'), light.get('value'), deep.get('value'),
        epoch_local_a_texto(dto.get('sleepStartTimestampLocal')),
        epoch_local_a_texto(dto.get('sleepEndTimestampLocal')),
        (stress or {}).get('avgStressLevel'), (stress or {}).get('maxStressLevel'),
        hrv_sum.get('weeklyAvg'), hrv_sum.get('lastNightAvg'), hrv_sum.get('lastNight5MinHigh'),
        hrv_sum.get('status'), baseline.get('lowUpper'), baseline.get('balancedUpper'),
        resting_hr,
        json.dumps(sleep, ensure_ascii=False) if sleep else None,
        json.dumps(stress, ensure_ascii=False) if stress else None,
        json.dumps(hrv, ensure_ascii=False) if hrv else None,
        json.dumps(rhr, ensure_ascii=False) if rhr else None,
    )


def sync_actividades(api, conn):
    conn.executescript(SCHEMA_ACTIVITIES)
    ids_existentes = {row[0] for row in conn.execute("SELECT activity_id FROM activities")}
    nuevas, start, limit, buscando = [], 0, 100, True
    while buscando:
        chunk = api.get_activities(start, limit)
        if not chunk:
            break
        for act in chunk:
            if act['activityId'] in ids_existentes:
                buscando = False
                break
            nuevas.append(act)
        start += limit

    if not nuevas:
        print("Actividades: ya está al día.")
        return

    placeholders_act = ",".join(["?"] * 40)

    def cadencia(a):
        return (a.get('averageRunningCadenceInStepsPerMinute')
                or a.get('averageBikingCadenceInRevPerMinute')
                or a.get('averageSwimCadenceInStrokesPerMinute'))

    def max_cadencia(a):
        return (a.get('maxRunningCadenceInStepsPerMinute')
                or a.get('maxBikingCadenceInRevPerMinute')
                or a.get('maxSwimCadenceInStrokesPerMinute'))

    def fila_actividad(a):
        return (
            a.get('activityId'), a.get('activityName'), a.get('description'),
            (a.get('activityType') or {}).get('typeKey'),
            a.get('startTimeLocal'), a.get('startTimeGMT'), a.get('locationName'),
            a.get('distance'), a.get('duration'), a.get('movingDuration'),
            a.get('elevationGain'), a.get('elevationLoss'),
            a.get('averageSpeed'), a.get('maxSpeed'), a.get('calories'),
            a.get('averageHR'), a.get('maxHR'),
            cadencia(a), max_cadencia(a), a.get('steps'),
            a.get('avgPower'), a.get('maxPower'), a.get('normPower'),
            a.get('aerobicTrainingEffect'), a.get('anaerobicTrainingEffect'),
            a.get('trainingEffectLabel'), a.get('activityTrainingLoad'),
            a.get('vO2MaxValue'), a.get('avgVerticalOscillation'),
            a.get('avgGroundContactTime'), a.get('avgStrideLength'),
            a.get('avgVerticalRatio'),
            a.get('hrTimeInZone_1'), a.get('hrTimeInZone_2'), a.get('hrTimeInZone_3'),
            a.get('hrTimeInZone_4'), a.get('hrTimeInZone_5'),
            a.get('lapCount'), a.get('deviceId'),
            json.dumps(a, ensure_ascii=False),
        )

    conn.executemany(f"INSERT OR IGNORE INTO activities VALUES ({placeholders_act})",
                      [fila_actividad(a) for a in nuevas])
    conn.commit()
    print(f"Actividades: {len(nuevas)} nuevas insertadas.")

    placeholders_lap = ",".join(["?"] * 25)

    def fila_lap(activity_id, lap):
        return (
            activity_id, lap.get('lapIndex'), lap.get('startTimeGMT'),
            lap.get('distance'), lap.get('duration'), lap.get('movingDuration'),
            lap.get('elevationGain'), lap.get('elevationLoss'),
            lap.get('averageSpeed'), lap.get('maxSpeed'),
            lap.get('calories'), lap.get('averageHR'), lap.get('maxHR'),
            lap.get('averageRunCadence'), lap.get('maxRunCadence'),
            lap.get('averagePower'), lap.get('maxPower'), lap.get('normalizedPower'),
            lap.get('groundContactTime'), lap.get('strideLength'),
            lap.get('verticalOscillation'), lap.get('verticalRatio'),
            lap.get('avgGradeAdjustedSpeed'), lap.get('intensityType'),
            json.dumps(lap, ensure_ascii=False),
        )

    for a in nuevas:
        aid = a.get('activityId')
        try:
            splits = api.get_activity_splits(aid)
            laps = splits.get('lapDTOs', [])
            conn.executemany(f"INSERT OR IGNORE INTO activity_laps VALUES ({placeholders_lap})",
                              [fila_lap(aid, l) for l in laps])
            conn.commit()
        except Exception as e:
            print(f"  laps de {aid}: error ({e})")
        time.sleep(PAUSA_ENTRE_LLAMADAS)


def sync_wellness(api, conn):
    """
    Always re-checks the last 3 days (not just "yesterday"): Garmin takes a
    while to finish processing sleep/stress/HRV for a day that has just
    ended, so a row saved half-complete the first time must be able to be
    updated in a later pass. That is why it uses INSERT OR REPLACE instead
    of INSERT OR IGNORE.
    """
    conn.executescript(SCHEMA_WELLNESS)
    placeholders = ",".join(["?"] * 36)
    hoy = date.today()
    for offset in range(3):
        fecha = hoy - timedelta(days=offset)
        fecha_str = fecha.isoformat()
        try:
            sleep = api.get_sleep_data(fecha_str)
            time.sleep(PAUSA_ENTRE_LLAMADAS)
            stress = api.get_stress_data(fecha_str)
            time.sleep(PAUSA_ENTRE_LLAMADAS)
            hrv = api.get_hrv_data(fecha_str)
            time.sleep(PAUSA_ENTRE_LLAMADAS)
            rhr = api.get_rhr_day(fecha_str)
            time.sleep(PAUSA_ENTRE_LLAMADAS)
            fila = extraer_fila_wellness(fecha_str, sleep, stress, hrv, rhr)
            conn.execute(f"INSERT OR REPLACE INTO daily_wellness VALUES ({placeholders})", fila)
            conn.commit()
        except Exception as e:
            print(f"  wellness {fecha_str}: error ({e})")
    print("Wellness: últimos 3 días actualizados.")


try:
    print(f"[{datetime.now(MADRID_TZ)}] Iniciando sincronización de Garmin...")
    conn = sqlite3.connect(DB_PATH)
    api = Garmin()
    api.login(TOKENSTORE)

    sync_actividades(api, conn)
    sync_wellness(api, conn)  # Comment out this line to deploy only the core, without wellness

    conn.close()
except Exception as e:
    print(f"Error en la sincronización: {e}")
