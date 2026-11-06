---
name: garmin-wellness
description: Usa este skill cuando el usuario pregunte sobre sueño, estrés, VFC (variabilidad de frecuencia cardíaca), frecuencia cardíaca en reposo, recuperación, o pida relacionar su rendimiento en carrera con cómo ha dormido/descansado. Conecta en modo SOLO LECTURA.
---

# Base de datos de bienestar diario

Ubicación: `/data/garmin.db`, tabla `daily_wellness` (una fila por día; el alcance exacto del histórico depende de desde cuándo se lanzó el backfill inicial — solo cubre datos de Garmin, no de fuentes importadas por separado como Apple Watch).

```python
import sqlite3
conn = sqlite3.connect('file:/data/garmin.db?mode=ro', uri=True)
```

## Columnas clave

- `calendar_date` TEXT PK (`'YYYY-MM-DD'`)
- Sueño: `sleep_time_s` (segundos totales), `deep_sleep_s`/`light_sleep_s`/`rem_sleep_s`/`awake_sleep_s`, `sleep_score` (0-100), `sleep_score_qualifier` (p. ej. GOOD/FAIR/EXCELLENT), `sleep_start_local`/`sleep_end_local`
- `avg_spo2`, `avg_respiration` — saturación de oxígeno y respiración durante el sueño
- `resting_hr` — FC en reposo del día (ppm)
- `avg_stress_level` / `max_stress_level` — 0-100, más alto = más estrés
- VFC: `hrv_weekly_avg`, `hrv_last_night_avg`, `hrv_status` (BALANCED/UNBALANCED/LOW/etc.)
- `raw_sleep`/`raw_stress`/`raw_hrv`/`raw_rhr` — JSON completo si necesitas algo no listado

## Para cruzar con rendimiento en carrera

La tabla `activities` (skill `garmin-coach`) tiene `start_time_local` — compara `DATE(start_time_local)` con `calendar_date` de `daily_wellness` del mismo día o el anterior (el sueño de la noche previa suele ser más relevante para una carrera matutina que el estrés del mismo día).

## Reglas

- Comprueba la fecha más antigua real disponible (`SELECT MIN(calendar_date) FROM daily_wellness`) antes de asumir cobertura total del historial — dilo si preguntan por fechas anteriores a esa.
- NUNCA intentes escribir — fallará, no lo sugieras.
