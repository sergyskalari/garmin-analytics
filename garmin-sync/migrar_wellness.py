"""INITIAL migration of the daily wellness history (sleep, stress, HRV,
resting heart rate) from Garmin Connect. Run it ONCE. It is not part of the
recurring Docker stack (sync.py covers that).

IMPORTANT about how far back the history goes: Garmin only has this data
from when you started using that specific device. If you are coming from
another watch (Apple Watch, Polar...), first check from which date there is
real data before launching the full backfill, or you will waste time
requesting empty days. Example check:

    from garminconnect import Garmin
    api = Garmin(); api.login('/app/data/tokens')
    for fecha in ['2025-01-01', '2025-02-01', '2025-02-15']:
        datos = api.get_sleep_data(fecha)
        print(fecha, datos.get('dailySleepDTO', {}).get('sleepTimeSeconds') is not None)

Adjust FECHA_INICIO (start date) below according to what you find.

Time warning: it makes 4 API calls PER DAY of history. With the configured
safety pause, expect roughly 40 minutes per year of history (about an hour
for the 600 days it was first run on). That is normal, it has not hung.

Usage:
    docker run -it --rm \
      -v "$DATA_ROOT/garmin-app/garmin_db:/app/data" \
      python:3.12-slim \
      bash -c "pip install --quiet garminconnect==0.3.16 && python3 /app/data/migrar_wellness.py"
"""

import sqlite3
import json
import time
from datetime import date, datetime, timedelta

DB_PATH = '/app/data/garmin.db'
TOKENSTORE = '/app/data/tokens'

# Adjust this date based on the check described in the docstring above.
FECHA_INICIO = date(2025, 2, 10)

PAUSA_ENTRE_LLAMADAS = 1.5

SCHEMA = """
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
    return datetime.utcfromtimestamp(ms / 1000).strftime('%Y-%m-%d %H:%M:%S')


def extraer_fila(fecha_str, sleep, stress, hrv, rhr):
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


from garminconnect import Garmin

api = Garmin()
api.login(TOKENSTORE)

conn = sqlite3.connect(DB_PATH)
conn.executescript(SCHEMA)

placeholders = ",".join(["?"] * 36)
dia = FECHA_INICIO
hoy = date.today()
total_dias = (hoy - FECHA_INICIO).days
procesados = 0

while dia <= hoy:
    fecha_str = dia.isoformat()
    try:
        sleep = api.get_sleep_data(fecha_str)
        time.sleep(PAUSA_ENTRE_LLAMADAS)
        stress = api.get_stress_data(fecha_str)
        time.sleep(PAUSA_ENTRE_LLAMADAS)
        hrv = api.get_hrv_data(fecha_str)
        time.sleep(PAUSA_ENTRE_LLAMADAS)
        rhr = api.get_rhr_day(fecha_str)
        time.sleep(PAUSA_ENTRE_LLAMADAS)

        fila = extraer_fila(fecha_str, sleep, stress, hrv, rhr)
        conn.execute(f"INSERT OR IGNORE INTO daily_wellness VALUES ({placeholders})", fila)
        conn.commit()
        procesados += 1
        if procesados % 20 == 0:
            print(f"[{procesados}/{total_dias}] hasta {fecha_str}...")
    except Exception as e:
        print(f"{fecha_str}: error ({e})")
    dia += timedelta(days=1)

total = conn.execute("SELECT COUNT(*) FROM daily_wellness").fetchone()[0]
print(f"Migración completada. {total} días en daily_wellness.")
conn.close()
