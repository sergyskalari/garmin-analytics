"""OPTIONAL, one-off import of Apple Watch history (exported from the iPhone
Health app) into the same garmin.db database. Meant for people who used an
Apple Watch before switching to Garmin -- run it ONCE; it is not part of any
recurring sync.

Prerequisites:
  1. Export from the iPhone: Health -> profile picture -> Export All Health
     Data -> Export. This produces a .zip containing export.xml.
  2. Copy export.xml to this host, into the folder you mount as
     /app/imports (see README).
  3. Adjust FECHA_INICIO / FECHA_FIN (start / end date) below to the period
     in which you actually used the Apple Watch.

Known limitations (not bugs, real limits of the data Apple Health exports):
  - No ground contact time / vertical oscillation / stride length on
    treadmill sessions (Apple only computes them with GPS active).
  - No elevation gain, cadence, per-session VO2Max, power or training effect
    -- Apple Health does not expose them in the standard export.
  - Average / max heart rate are computed here by matching the loose
    <Record type="HeartRate"> samples against the time window of each
    <Workout>; they are not a value the watch computed directly.

Usage:
    docker run -it --rm \
      -v "$DATA_ROOT/garmin-app/garmin_db:/app/data" \
      -v "$DATA_ROOT/garmin-app/imports:/app/imports" \
      python:3.12-slim \
      python3 /app/data/importar_apple.py
"""

import xml.etree.ElementTree as ET
import sqlite3
import json
from datetime import datetime, timezone
from bisect import bisect_right

XML_PATH = '/app/imports/export.xml'
DB_PATH = '/app/data/garmin.db'

# Adjust to the period in which you used your Apple Watch.
FECHA_INICIO = datetime.fromisoformat('2022-11-15').date()
FECHA_FIN = datetime.fromisoformat('2025-02-04').date()


def parse_apple_date(s):
    return datetime.strptime(s, '%Y-%m-%d %H:%M:%S %z')


print("Pasada 1: localizando carreras del Apple Watch...")
workouts = []
for event, elem in ET.iterparse(XML_PATH, events=('end',)):
    if elem.tag == 'Workout' and elem.get('workoutActivityType') == 'HKWorkoutActivityTypeRunning':
        start_dt = parse_apple_date(elem.get('startDate'))
        end_dt = parse_apple_date(elem.get('endDate'))
        if FECHA_INICIO <= start_dt.date() <= FECHA_FIN:
            distancia_km = 0.0
            calorias = 0.0
            for stat in elem.findall('WorkoutStatistics'):
                if stat.get('type') == 'HKQuantityTypeIdentifierDistanceWalkingRunning':
                    distancia_km = float(stat.get('sum', 0))
                elif stat.get('type') == 'HKQuantityTypeIdentifierActiveEnergyBurned':
                    calorias = float(stat.get('sum', 0))
            workouts.append({
                'id': -int(start_dt.strftime('%Y%m%d%H%M%S')),
                'start_dt': start_dt,
                'end_dt': end_dt,
                'distancia_km': distancia_km,
                'calorias': calorias,
                'duracion_min': float(elem.get('duration', 0)),
                'hr_samples': [], 'gct_samples': [], 'vo_samples': [], 'stride_samples': [],
            })
        elem.clear()
    elif elem.tag == 'Record':
        elem.clear()

workouts.sort(key=lambda w: w['start_dt'])
starts = [w['start_dt'] for w in workouts]
print(f"Encontradas {len(workouts)} carreras entre {FECHA_INICIO} y {FECHA_FIN}.")

print("Pasada 2: cruzando muestras de FC, GCT, oscilación vertical y zancada...")
TIPOS = {
    'HKQuantityTypeIdentifierHeartRate': 'hr_samples',
    'HKQuantityTypeIdentifierRunningGroundContactTime': 'gct_samples',
    'HKQuantityTypeIdentifierRunningVerticalOscillation': 'vo_samples',
    'HKQuantityTypeIdentifierRunningStrideLength': 'stride_samples',
}
contador = 0
for event, elem in ET.iterparse(XML_PATH, events=('end',)):
    if elem.tag == 'Record':
        tipo = elem.get('type')
        if tipo in TIPOS:
            try:
                rec_dt = parse_apple_date(elem.get('startDate'))
                valor = float(elem.get('value'))
            except (TypeError, ValueError):
                elem.clear()
                continue
            idx = bisect_right(starts, rec_dt) - 1
            if idx >= 0 and workouts[idx]['start_dt'] <= rec_dt <= workouts[idx]['end_dt']:
                workouts[idx][TIPOS[tipo]].append(valor)
                contador += 1
        elem.clear()
    elif elem.tag == 'Workout':
        elem.clear()
print(f"{contador} muestras asignadas.")


def media(lista):
    return round(sum(lista) / len(lista), 2) if lista else None


def maximo(lista):
    return round(max(lista), 2) if lista else None


conn = sqlite3.connect(DB_PATH)
# Assumes `activities` already exists (created by the core's sync.py / migrar.py).
# If you run this against a fresh database, first create the activities table
# using the schema in garmin-sync/sync.py.
placeholders = ",".join(["?"] * 40)
filas = []
for w in workouts:
    distancia_m = w['distancia_km'] * 1000
    duracion_s = w['duracion_min'] * 60
    velocidad_media = (distancia_m / duracion_s) if duracion_s > 0 else None
    vo_medio = media(w['vo_samples'])
    gct_medio = media(w['gct_samples'])
    stride_medio_cm = media([s * 100 for s in w['stride_samples']])
    vertical_ratio = round((vo_medio / stride_medio_cm) * 100, 2) if (vo_medio and stride_medio_cm) else None

    filas.append((
        w['id'], 'Carrera (Apple Watch)', None, 'running',
        w['start_dt'].strftime('%Y-%m-%d %H:%M:%S'),
        w['start_dt'].astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
        None,
        distancia_m, duracion_s, duracion_s,
        None, None,
        velocidad_media, None,
        w['calorias'],
        media(w['hr_samples']), maximo(w['hr_samples']),
        None, None, None,
        None, None, None,
        None, None, None, None,
        None, vo_medio, gct_medio, stride_medio_cm, vertical_ratio,
        None, None, None, None, None,
        None, None,
        json.dumps({
            'distancia_km': w['distancia_km'], 'calorias': w['calorias'],
            'n_muestras_hr': len(w['hr_samples']), 'n_muestras_gct': len(w['gct_samples']),
        }, ensure_ascii=False),
    ))

conn.executemany(f"INSERT OR IGNORE INTO activities VALUES ({placeholders})", filas)
conn.commit()
total = conn.execute("SELECT COUNT(*) FROM activities WHERE activity_id < 0").fetchone()[0]
print(f"Importación completada. {total} carreras del Apple Watch en la base de datos.")
conn.close()
