🇪🇸 [Leer en español](README.es.md)

# Grafana dashboards

`dashboard.json` is the "Garmin - Running" dashboard exported as-is from Grafana, with its 6 panels. The SQL query behind each one is also documented in this file, so you can understand them without opening the JSON or rebuild a panel by hand.

**How to import it:** *Dashboards → New → Import*, upload `dashboard.json` and save. The queries carry no datasource identifier: they refer to the `frser-sqlite-datasource` plugin by its type, so there is nothing to reassign, but that datasource must exist first (*Connections → Data sources*, path `/app/data/garmin.db`).

**Requirements and limits worth knowing:**
- It uses Grafana's new dashboard format (`apiVersion: dashboard.grafana.app/v2`), not the classic JSON, so it needs a recent version of Grafana. It was exported from an instance running `grafana/grafana-oss:latest` in October 2026.
- It has not been tested by importing it into a clean instance. If that fails, the queries in this file are enough to rebuild the panels.
- It is an unedited copy of the export: it includes the dashboard's internal identifier (`metadata.name`) and the creation date of the original instance, neither of which affects how it works.
- It contains no personal data or credentials (checked before adding it).

The series names and panel titles inside the dashboard are in Spanish (e.g. `Ritmo (min/km)`, `Kilómetros`), so the SQL aliases below keep the Spanish text to match the exported file exactly.

Datasource: [`frser-sqlite-datasource`](https://grafana.com/grafana/plugins/frser-sqlite-datasource/) plugin (installed through `GF_INSTALL_PLUGINS` in the compose file), pointing to `/app/data/garmin.db`.

**Important technical detail:** SQLite has no native "time" type. For a time-series panel to work, the date column must be converted to RFC3339 in the query itself, using the GMT column (not the local one) so the UTC `Z` suffix is correct — Grafana converts it to your local time automatically when displaying it.

**Activity type filter.** `activity_type` has several values (strength, cycling, padel...) and four running ones: `running`, `trail_running`, `ultra_run` and `treadmill_running`. Check your own with `SELECT activity_type, COUNT(*) FROM activities GROUP BY 1`. All panels use the four running types, except the **ground contact time** series, which uses only `running`. If you filter on `'running'` alone in the volume panels, trails and ultras are silently left out. Keep in mind that the pace of a trail or an ultra (9-10 min/km in an ultra) is not comparable with a road run: in the pace panel it stretches the axis and flattens the trend. If you would rather see road runs only, change that panel's filter to `activity_type = 'running'`.

## Pace trend

**Time series** panel. A lower value means better performance.

```sql
SELECT
  REPLACE(start_time_gmt, ' ', 'T') || 'Z' AS time,
  ROUND((1000.0 / average_speed_mps) / 60.0, 2) AS "Ritmo (min/km)"
FROM activities
WHERE activity_type IN ('running', 'trail_running', 'ultra_run', 'treadmill_running')
  AND average_speed_mps > 0
ORDER BY start_time_gmt ASC
```

## Weekly volume (km)

**Time series** panel with `Graph styles → Style: Bars` (do not use the "Bar chart" panel type: it is designed for text categories, not dates, and reorders the data badly).

```sql
SELECT
  date(start_time_gmt, 'weekday 0', '-6 days') || 'T00:00:00Z' AS time,
  ROUND(SUM(distance_m) / 1000.0, 1) AS "Kilómetros"
FROM activities
WHERE activity_type IN ('running', 'trail_running', 'ultra_run', 'treadmill_running')
GROUP BY date(start_time_gmt, 'weekday 0', '-6 days')
ORDER BY time ASC
```

## Weekly elevation gain (m)

Same pattern as the previous one (Time series + Bars style):

```sql
SELECT
  date(start_time_gmt, 'weekday 0', '-6 days') || 'T00:00:00Z' AS time,
  ROUND(SUM(elevation_gain_m), 0) AS "Desnivel positivo (m)"
FROM activities
WHERE activity_type IN ('running', 'trail_running', 'ultra_run', 'treadmill_running')
GROUP BY date(start_time_gmt, 'weekday 0', '-6 days')
ORDER BY time ASC
```

## Cadence and ground contact time

Two queries (A and B) in the same **Time series** panel. Under `Overrides`, add a field override on the ground contact time series with the property `Axis placement: Right`, so each metric gets its own scale.

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

## Kilometers this year

**Stat** panel. It needs no time handling (a single number):

```sql
SELECT
  ROUND(SUM(distance_m) / 1000.0, 1) AS "Kilómetros"
FROM activities
WHERE activity_type IN ('running', 'trail_running', 'ultra_run', 'treadmill_running')
  AND strftime('%Y', start_time_gmt) = strftime('%Y', 'now')
```

## Kilometers per year (full history)

**Bar chart** panel (here it does fit: the year is a text category, not a date, so the bar-specialized panel type works well). Since it does not use a `time` column, it does not depend on the dashboard's time-range picker — it always shows the full history:

```sql
SELECT
  strftime('%Y', start_time_gmt) AS "Año",
  ROUND(SUM(distance_m) / 1000.0, 1) AS "Kilómetros"
FROM activities
WHERE activity_type IN ('running', 'trail_running', 'ultra_run', 'treadmill_running')
GROUP BY strftime('%Y', start_time_gmt)
ORDER BY "Año" ASC
```
