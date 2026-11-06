"""INITIAL migration of the full activity + lap history from Garmin Connect.
Run it ONCE, before starting the recurring sync (sync.py). It is not part of
the recurring Docker stack.

It downloads the ENTIRE history straight from the API (paginated, 100 at a
time) and also writes a JSON dump (actividades.json) as a backup copy of the
initial download. The code never reads that file back; it is only there in
case you want to inspect it.

Requires tokens already generated in /app/data/tokens (see README,
authentication step).

Usage:
    docker run -it --rm \
      -v "$DATA_ROOT/garmin-app/garmin_db:/app/data" \
      python:3.12-slim \
      bash -c "pip install --quiet garminconnect==0.3.16 && python3 /app/data/migrar.py"
"""

import sqlite3
import json
import time

DB_PATH = '/app/data/garmin.db'
JSON_PATH = '/app/data/actividades.json'  # backup copy: it is written, never read
PAUSA_ENTRE_LLAMADAS = 1.5

SCHEMA = """
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


def cadencia(act):
    return (act.get('averageRunningCadenceInStepsPerMinute')
            or act.get('averageBikingCadenceInRevPerMinute')
            or act.get('averageSwimCadenceInStrokesPerMinute'))


def max_cadencia(act):
    return (act.get('maxRunningCadenceInStepsPerMinute')
            or act.get('maxBikingCadenceInRevPerMinute')
            or act.get('maxSwimCadenceInStrokesPerMinute'))


def fila_actividad(act):
    return (
        act.get('activityId'), act.get('activityName'), act.get('description'),
        (act.get('activityType') or {}).get('typeKey'),
        act.get('startTimeLocal'), act.get('startTimeGMT'), act.get('locationName'),
        act.get('distance'), act.get('duration'), act.get('movingDuration'),
        act.get('elevationGain'), act.get('elevationLoss'),
        act.get('averageSpeed'), act.get('maxSpeed'), act.get('calories'),
        act.get('averageHR'), act.get('maxHR'),
        cadencia(act), max_cadencia(act), act.get('steps'),
        act.get('avgPower'), act.get('maxPower'), act.get('normPower'),
        act.get('aerobicTrainingEffect'), act.get('anaerobicTrainingEffect'),
        act.get('trainingEffectLabel'), act.get('activityTrainingLoad'),
        act.get('vO2MaxValue'), act.get('avgVerticalOscillation'),
        act.get('avgGroundContactTime'), act.get('avgStrideLength'),
        act.get('avgVerticalRatio'),
        act.get('hrTimeInZone_1'), act.get('hrTimeInZone_2'), act.get('hrTimeInZone_3'),
        act.get('hrTimeInZone_4'), act.get('hrTimeInZone_5'),
        act.get('lapCount'), act.get('deviceId'),
        json.dumps(act, ensure_ascii=False),
    )


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


from garminconnect import Garmin

api = Garmin()
api.login('/app/data/tokens')

conn = sqlite3.connect(DB_PATH)
conn.executescript(SCHEMA)

print("Descargando historial completo de actividades desde Garmin Connect...")
actividades = []
start, limit = 0, 100
while True:
    print(f"Consultando actividades desde la {start} a la {start + limit}...")
    chunk = api.get_activities(start, limit)
    if not chunk:
        break
    actividades.extend(chunk)
    start += limit

with open(JSON_PATH, 'w') as f:
    json.dump(actividades, f, ensure_ascii=False)
print(f"{len(actividades)} actividades descargadas (copia de respaldo en {JSON_PATH}).")

placeholders_act = ",".join(["?"] * 40)
conn.executemany(
    f"INSERT OR IGNORE INTO activities VALUES ({placeholders_act})",
    [fila_actividad(a) for a in actividades]
)
conn.commit()
print(f"Resumen de {len(actividades)} actividades insertado.")

placeholders_lap = ",".join(["?"] * 25)
total = len(actividades)
for i, act in enumerate(actividades, 1):
    activity_id = act.get('activityId')
    try:
        splits = api.get_activity_splits(activity_id)
        laps = splits.get('lapDTOs', [])
        conn.executemany(
            f"INSERT OR IGNORE INTO activity_laps VALUES ({placeholders_lap})",
            [fila_lap(activity_id, lap) for lap in laps]
        )
        conn.commit()
        print(f"[{i}/{total}] activity {activity_id}: {len(laps)} laps guardados.")
    except Exception as e:
        print(f"[{i}/{total}] activity {activity_id}: error al pedir splits ({e})")
    time.sleep(PAUSA_ENTRE_LLAMADAS)

print("Migración de laps completada.")
conn.close()
