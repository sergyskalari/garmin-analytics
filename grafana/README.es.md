🇬🇧 [Read this in English](README.md)

# Dashboards de Grafana

`dashboard.json` es el dashboard "Garmin - Running" exportado tal cual desde Grafana, con sus 6 paneles. Las consultas SQL de cada uno se documentan además en este archivo, para entenderlas sin abrir el JSON o reconstruir un panel a mano.

**Cómo importarlo:** *Dashboards → New → Import*, subir `dashboard.json` y guardar. Las consultas no llevan ningún identificador de datasource: se refieren al plugin `frser-sqlite-datasource` por su tipo, así que no hay nada que reasignar, pero ese datasource debe existir antes (*Connections → Data sources*, ruta `/app/data/garmin.db`).

**Requisitos y límites que conviene saber:**
- Está en el formato nuevo de Grafana (`apiVersion: dashboard.grafana.app/v2`), no en el JSON clásico, así que necesita una versión reciente de Grafana. Se exportó de una instancia con `grafana/grafana-oss:latest` en octubre de 2026.
- No se ha probado a importarlo en una instancia limpia. Si falla, las consultas de este archivo bastan para reconstruir los paneles.
- Es una copia de lo exportado, sin editar: incluye el identificador interno del dashboard (`metadata.name`) y la fecha de creación de la instancia original, que no afectan al funcionamiento.
- No contiene datos personales ni credenciales (revisado antes de añadirlo).

Datasource: plugin [`frser-sqlite-datasource`](https://grafana.com/grafana/plugins/frser-sqlite-datasource/) (se instala solo vía `GF_INSTALL_PLUGINS` en el compose), apuntando a `/app/data/garmin.db`.

**Detalle técnico importante:** SQLite no tiene tipo de dato "tiempo" nativo. Para que un panel de serie temporal funcione, la columna de fecha debe convertirse a RFC3339 en la propia consulta, usando la columna GMT (no local) para que la `Z` de UTC sea correcta — Grafana la convierte a tu hora local automáticamente al mostrarla.

**Filtro por tipo de actividad.** `activity_type` tiene varios valores (fuerza, ciclismo, pádel...) y cuatro de carrera: `running`, `trail_running`, `ultra_run` y `treadmill_running`. Comprueba los tuyos con `SELECT activity_type, COUNT(*) FROM activities GROUP BY 1`. Todos los paneles usan los cuatro tipos de carrera, salvo la serie de **contacto con el suelo**, que usa solo `running`. Si filtras solo por `'running'` en los de volumen, dejas fuera trails y ultras sin ningún aviso. Ten en cuenta que el ritmo de un trail o un ultra (9-10 min/km en un ultra) no es comparable con el de una tirada en calle: en el panel de ritmo estira el eje y aplasta la tendencia. Si prefieres ver solo calle, cambia el filtro de ese panel a `activity_type = 'running'`.

## Evolución del ritmo

Panel tipo **Time series**. A menor valor, mejor rendimiento.

```sql
SELECT
  REPLACE(start_time_gmt, ' ', 'T') || 'Z' AS time,
  ROUND((1000.0 / average_speed_mps) / 60.0, 2) AS "Ritmo (min/km)"
FROM activities
WHERE activity_type IN ('running', 'trail_running', 'ultra_run', 'treadmill_running')
  AND average_speed_mps > 0
ORDER BY start_time_gmt ASC
```

## Volumen semanal (km)

Panel tipo **Time series**, con `Graph styles → Style: Bars` (no uses el tipo de panel "Bar chart": está pensado para categorías de texto, no fechas, y reordena mal los datos).

```sql
SELECT
  date(start_time_gmt, 'weekday 0', '-6 days') || 'T00:00:00Z' AS time,
  ROUND(SUM(distance_m) / 1000.0, 1) AS "Kilómetros"
FROM activities
WHERE activity_type IN ('running', 'trail_running', 'ultra_run', 'treadmill_running')
GROUP BY date(start_time_gmt, 'weekday 0', '-6 days')
ORDER BY time ASC
```

## Desnivel positivo semanal (m)

Mismo patrón que el anterior (Time series + estilo Bars):

```sql
SELECT
  date(start_time_gmt, 'weekday 0', '-6 days') || 'T00:00:00Z' AS time,
  ROUND(SUM(elevation_gain_m), 0) AS "Desnivel positivo (m)"
FROM activities
WHERE activity_type IN ('running', 'trail_running', 'ultra_run', 'treadmill_running')
GROUP BY date(start_time_gmt, 'weekday 0', '-6 days')
ORDER BY time ASC
```

## Cadencia y contacto con el suelo

Dos queries (A y B) en el mismo panel **Time series**. En `Overrides`, añade un override de campo sobre la serie de contacto con el suelo con la propiedad `Axis placement: Right`, para que cada métrica tenga su propia escala.

Query A:
```sql
SELECT
  REPLACE(start_time_gmt, ' ', 'T') || 'Z' AS time,
  ROUND(average_cadence, 1) AS "Cadencia (ppm)"
FROM activities
WHERE activity_type IN ('running', 'trail_running', 'ultra_run', 'treadmill_running')
  AND average_cadence IS NOT NULL
ORDER BY start_time_gmt ASC
```

Query B:
```sql
SELECT
  REPLACE(start_time_gmt, ' ', 'T') || 'Z' AS time,
  ROUND(avg_ground_contact_time, 1) AS "Contacto con el suelo (ms)"
FROM activities
WHERE activity_type = 'running'
  AND avg_ground_contact_time IS NOT NULL
ORDER BY start_time_gmt ASC
```

## Km del año en curso

Panel tipo **Stat**. No necesita tratamiento de tiempo (un único número):

```sql
SELECT
  ROUND(SUM(distance_m) / 1000.0, 1) AS "Kilómetros"
FROM activities
WHERE activity_type IN ('running', 'trail_running', 'ultra_run', 'treadmill_running')
  AND strftime('%Y', start_time_gmt) = strftime('%Y', 'now')
```

## Kilómetros por año (histórico completo)

Panel tipo **Bar chart** (aquí sí: el año es una categoría de texto, no una fecha, así que el tipo de panel especializado en barras funciona bien). Como no usa una columna `time`, no depende del selector de rango del dashboard — siempre muestra el histórico completo:

```sql
SELECT
  strftime('%Y', start_time_gmt) AS "Año",
  ROUND(SUM(distance_m) / 1000.0, 1) AS "Kilómetros"
FROM activities
WHERE activity_type IN ('running', 'trail_running', 'ultra_run', 'treadmill_running')
GROUP BY strftime('%Y', start_time_gmt)
ORDER BY "Año" ASC
```
