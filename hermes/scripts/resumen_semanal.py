#!/usr/bin/env python3
"""
Computes the weekly running statistics for the Hermes weekly-summary cron job.

Hermes injects this script's stdout into the agent prompt (cron --script), so
the LLM only has to write the text: it does no arithmetic and needs no code
execution, which is blocked in unattended cron runs anyway.

Read-only: the database is opened with mode=ro.
Test overrides: GARMIN_DB (path to the database) and RESUMEN_HOY (YYYY-MM-DD).
"""
import os
import sqlite3
from datetime import date, timedelta

DB = os.environ.get('GARMIN_DB', '/data/garmin.db')
RUN_TYPES = ('running', 'trail_running', 'ultra_run', 'treadmill_running')
DIAS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
OBJETIVO = os.path.join(os.environ.get('HERMES_HOME', '/opt/data'), 'skills/garmin-coach/objetivo.md')


def mmss(seg_por_km):
    s = int(round(seg_por_km))
    return f"{s // 60}:{s % 60:02d}"


def lunes(d):
    return d - timedelta(days=d.weekday())


def estadisticas(filas):
    """filas: (tipo, distancia_m, duracion_s, fc_media). Pace only from 'running' (road)."""
    km = sum(f[1] for f in filas) / 1000.0
    calle = [f for f in filas if f[0] == 'running']
    d_calle = sum(f[1] for f in calle)
    ritmo = (sum(f[2] for f in calle) / (d_calle / 1000.0)) if d_calle > 0 else None
    con_fc = [f for f in filas if f[3] is not None and f[2]]
    fc = (sum(f[3] * f[2] for f in con_fc) / sum(f[2] for f in con_fc)) if con_fc else None
    return {'n': len(filas), 'km': km, 'ritmo': ritmo, 'fc': fc}


def texto(e):
    r = f"{mmss(e['ritmo'])} /km" if e['ritmo'] else "sin carreras en calle"
    fc = f"{e['fc']:.0f} ppm" if e['fc'] else "sin datos"
    return f"{e['n']} carreras, {e['km']:.1f} km, ritmo en calle {r}, FC media {fc}"


def main():
    hoy = date.fromisoformat(os.environ['RESUMEN_HOY']) if os.environ.get('RESUMEN_HOY') else date.today()
    # On Sunday the cron runs at the end of the week: that week counts as complete.
    analizada = lunes(hoy) if hoy.weekday() == 6 else lunes(hoy) - timedelta(days=7)
    semanas = [analizada - timedelta(days=7 * i) for i in range(6)]  # 0 = analysed, 1..5 = previous
    desde = semanas[-1].isoformat()

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    marcas = ",".join("?" * len(RUN_TYPES))
    carreras = con.execute(
        f"SELECT substr(start_time_local,1,10), activity_type, distance_m, duration_s, average_hr "
        f"FROM activities WHERE activity_type IN ({marcas}) AND distance_m > 0 AND start_time_local >= ? "
        f"ORDER BY start_time_local", (*RUN_TYPES, desde)).fetchall()
    fuerza = [r[0] for r in con.execute(
        "SELECT substr(start_time_local,1,10) FROM activities WHERE activity_type='strength_training' "
        "AND start_time_local >= ?", (desde,))]

    por_semana = {s: [] for s in semanas}
    for f, tipo, dist, dur, fc in carreras:
        lun = lunes(date.fromisoformat(f))
        if lun in por_semana:
            por_semana[lun].append((tipo, dist, dur, fc))
    est = {s: estadisticas(v) for s, v in por_semana.items()}

    w0, w1 = est[semanas[0]], est[semanas[1]]
    previas = [f for s in semanas[1:4] for f in por_semana[s]]
    media_km = sum(est[s]['km'] for s in semanas[1:4]) / 3
    ep = estadisticas(previas)

    print("DATOS YA CALCULADOS (úsalos tal cual, no recalcules nada)")
    print(f"Hoy: {DIAS[hoy.weekday()]} {hoy.isoformat()}. Semana analizada: lunes {analizada.isoformat()} "
          f"a domingo {(analizada + timedelta(days=6)).isoformat()}"
          f"{' (completa)' if hoy.weekday() == 6 else ' (completa; la semana en curso aún no ha terminado)'}.")
    print()
    for i, s in enumerate(semanas[:4]):
        etiqueta = ['Semana analizada', 'Semana anterior', 'Hace 2 semanas', 'Hace 3 semanas'][i]
        print(f"- {etiqueta} (lunes {s.isoformat()}): {texto(est[s])}")
    print()
    print("Comparación:")
    if w1['km'] > 0:
        print(f"- Volumen vs semana anterior: {(w0['km'] / w1['km'] - 1) * 100:+.0f} % ({w0['km']:.1f} km frente a {w1['km']:.1f} km)")
    print(f"- Volumen: media de las 3 semanas previas = {media_km:.1f} km (las semanas sin carreras cuentan como 0)")
    if w0['ritmo'] and w1['ritmo']:
        dif = w0['ritmo'] - w1['ritmo']
        print(f"- Ritmo en calle vs semana anterior: " + ("igual" if round(dif) == 0 else f"{abs(dif):.0f} s/km {'más rápido' if dif < 0 else 'más lento'}"))
    if w0['ritmo'] and ep['ritmo']:
        print(f"- Ritmo en calle: media ponderada de las 3 semanas previas = {mmss(ep['ritmo'])} /km")
    if ep['fc']:
        print(f"- FC media ponderada de las 3 semanas previas = {ep['fc']:.0f} ppm")

    print()
    print(f"Carreras de la semana analizada (calle y resto):")
    for f, tipo, dist, dur, fc in carreras:
        if lunes(date.fromisoformat(f)) == analizada:
            fcs = f"{fc:.0f} ppm" if fc else "sin FC"
            print(f"  · {DIAS[date.fromisoformat(f).weekday()]} {f} [{tipo}] {dist / 1000:.1f} km, {mmss(dur / (dist / 1000.0))} /km, {fcs}")

    def por_dia(fechas):
        c = [0] * 7
        for f in fechas:
            c[date.fromisoformat(f).weekday()] += 1
        return ", ".join(f"{DIAS[i]} {c[i]}" for i in range(7) if c[i]) or "ninguno"

    print()
    print("Patrón real de las últimas 6 semanas (de lunes {} a domingo {}):".format(
        semanas[-1].isoformat(), (analizada + timedelta(days=6)).isoformat()))
    print(f"- Carreras: {len(carreras)} en total ({len(carreras) / 6:.1f} por semana). Por día: {por_dia([c[0] for c in carreras])}")
    print(f"- Fuerza: {len(fuerza)} sesiones. Por día: {por_dia(fuerza)}")
    print()
    if os.path.exists(OBJETIVO):
        print("Objetivo del usuario:")
        print(open(OBJETIVO, encoding='utf-8').read().strip())
    else:
        print("Objetivo del usuario: NO hay ninguno definido.")


if __name__ == '__main__':
    try:
        main()
    except Exception as e:  # make failures visible in the prompt instead of silent
        print(f"ERROR al calcular los datos: {e}. No inventes cifras; avisa del error.")
