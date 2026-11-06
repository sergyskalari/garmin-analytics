---
name: garmin-coach
description: Usa este skill cuando el usuario pregunte sobre sus carreras, entrenamientos, ritmo, frecuencia cardíaca, cadencia, VO2Max, desnivel, volumen semanal/anual, o pida análisis/comparación de sesiones de running, O pida sincronizar/actualizar los datos/traer la carrera de hoy ahora mismo. Conecta en modo SOLO LECTURA a la base de datos combinada de Garmin (+ Apple Watch si se importó ese histórico); la sincronización bajo demanda usa un webhook separado (ver más abajo).
---

# Base de datos de running

Ubicación: `/data/garmin.db` (SQLite, montado en modo solo lectura a nivel de sistema de ficheros — cualquier intento de escritura falla, es intencionado).

```python
import sqlite3
conn = sqlite3.connect('file:/data/garmin.db?mode=ro', uri=True)
```

## Tabla `activities` — una fila por sesión

- `activity_id` INTEGER PK — positivo = Garmin, negativo = importado de otra fuente (p. ej. Apple Watch)
- `activity_name`, `description`, `activity_type` TEXT
- `start_time_local`, `start_time_gmt` TEXT (`'YYYY-MM-DD HH:MM:SS'`)
- `distance_m`, `duration_s`, `moving_duration_s` REAL
- `elevation_gain_m`, `elevation_loss_m` REAL (puede ser NULL según la fuente)
- `average_speed_mps`, `max_speed_mps` REAL — ritmo en min/km = `1000 / average_speed_mps / 60`
- `calories` REAL
- `average_hr`, `max_hr` REAL (ppm)
- `average_cadence`, `max_cadence` REAL (puede ser NULL según la fuente)
- `steps` INTEGER
- `avg_power`, `max_power`, `norm_power` REAL (solo disponible en algunas fuentes)
- `aerobic_training_effect`, `anaerobic_training_effect`, `training_effect_label`, `activity_training_load`
- `vo2max_value` REAL (no todas las sesiones lo traen)
- `avg_vertical_oscillation`, `avg_ground_contact_time`, `avg_stride_length`, `avg_vertical_ratio` — pueden ser NULL en sesiones de cinta sin GPS, según la fuente
- `hr_zone_1` a `hr_zone_5` REAL (segundos en cada zona)
- `lap_count`, `device_id` INTEGER
- `raw_json` TEXT — JSON completo original, úsalo si necesitas un campo no listado aquí

## Tipos de actividad

`activity_type` toma varios valores en esta base: fuerza (`strength_training`), ciclismo en sala, pádel, natación, remo, caminatas y senderismo, entre otros, además de cuatro tipos de carrera:

- `running` — carrera en calle/carretera (también todas las importadas de Apple Watch)
- `trail_running` — carrera de montaña
- `ultra_run` — ultras (p. ej. el Gran Trail de Gredos)
- `treadmill_running` — cinta

Comprueba siempre los valores reales con `SELECT activity_type, COUNT(*) FROM activities GROUP BY 1` antes de afirmar qué tipos hay.

Reglas al filtrar:
- Para "carreras", "kilómetros corridos" o "volumen", usa `activity_type IN ('running', 'trail_running', 'ultra_run', 'treadmill_running')`. NUNCA filtres por `activity_name` (p. ej. "Madrid Carrera"): deja fuera sesiones con otro nombre, como los trails y ultras.
- Para comparar **ritmo** entre sesiones, `running` (calle) es lo comparable; el ritmo de un trail o un ultra no es comparable con el de una tirada en calle. Si los incluyes, dilo y márcalos aparte.
- Si en un análisis excluyes algún tipo de carrera, dilo explícitamente.

## Tabla `activity_laps` — una fila por lap dentro de una sesión

Clave primaria compuesta `(activity_id, lap_index)`. Columnas: `start_time_gmt`, `distance_m`, `duration_s`, `moving_duration_s`, `elevation_gain_m`, `elevation_loss_m`, `average_speed_mps`, `max_speed_mps`, `calories`, `average_hr`, `max_hr`, `average_cadence`, `max_cadence`, `average_power`, `max_power`, `normalized_power`, `ground_contact_time`, `stride_length`, `vertical_oscillation`, `vertical_ratio`, `avg_grade_adjusted_speed`, `intensity_type`, `raw_json`.

## Reglas

- NUNCA intentes INSERT/UPDATE/DELETE — fallarán igualmente (la base está montada en solo lectura), pero no lo intentes ni lo sugieras.
- Si un campo sale NULL, dilo explícitamente en vez de omitirlo silenciosamente — puede ser información relevante (p. ej. "no tengo cadencia de esta sesión porque esa fuente no la exporta").
- Para ritmo, recuerda al usuario que valores más bajos = mejor rendimiento (al contrario que la mayoría de métricas donde "más alto" es mejor).
- Si el usuario tiene definido un objetivo de entrenamiento (archivo `objetivo.md` en esta misma carpeta, si existe), tenlo en cuenta al proponer planificación o valorar progreso.

## Sincronización bajo demanda (opcional)

Solo si el stack incluye el servicio `garmin-sync-webhook`. Si el usuario pide sincronizar ahora / actualizar datos / traer la carrera de hoy sin esperar al ciclo automático, usa la herramienta de terminal para ejecutar:

```bash
curl -s -X POST http://127.0.0.1:8099/sync -H "X-Webhook-Token: $(cat /opt/data/secrets/webhook_token.txt)"
```

Esto tarda unos segundos (consulta real a Garmin). Informa al usuario del resultado (actividades nuevas encontradas o "ya está al día"), pero NUNCA repitas el contenido del token en tu respuesta.

## Cálculos y formato

- Ritmos: escribe SIEMPRE min:seg por km (4:46 /km). Nunca minutos decimales: 4,77 min son 4:46, no 4:77. Desde segundos por km: minutos = seg // 60, segundos = seg % 60.
- Media de ritmo de varias carreras: NO promedies los ritmos ni las velocidades de cada carrera. Usa tiempo total / distancia total: SUM(duration_s) / (SUM(distance_m) / 1000) = segundos por km.
- FC media de un periodo: ponderada por duración, ignorando las filas sin FC.
- Semanas: siempre de lunes a domingo y cada una por separado. "Hace dos semanas" es UNA semana, no un rango de 14 días. Calcula primero la fecha de hoy y a qué lunes corresponde: si hoy no es domingo, la semana en curso está incompleta (dilo); si no aparece en el resultado porque aún no hay carreras, no la sustituyas por la anterior.
- Medias, sumas y porcentajes de comparación (por ejemplo "media de las tres semanas previas" o una variación en %) se calculan con código (SQL o Python), nunca de cabeza. Indica sobre qué semanas calculas cada media.
- Volumen: usa los cuatro tipos de carrera. Ritmo: solo `running` (calle).

Consulta semanal de referencia (últimas 4 semanas):

    SELECT date(start_time_local, 'weekday 0', '-6 days') AS semana_lunes,
      COUNT(*) AS carreras,
      ROUND(SUM(distance_m) / 1000.0, 1) AS km_total,
      CAST(ROUND(SUM(CASE WHEN activity_type = 'running' THEN duration_s END)
        / (SUM(CASE WHEN activity_type = 'running' THEN distance_m END) / 1000.0)) AS INTEGER) AS seg_por_km_calle,
      ROUND(SUM(CASE WHEN average_hr IS NOT NULL THEN average_hr * duration_s END)
        / SUM(CASE WHEN average_hr IS NOT NULL THEN duration_s END)) AS fc_media
    FROM activities
    WHERE activity_type IN ('running', 'trail_running', 'ultra_run', 'treadmill_running')
      AND distance_m > 0
    GROUP BY semana_lunes ORDER BY semana_lunes DESC LIMIT 4
