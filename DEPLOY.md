## 1. Despliegue del núcleo (extractor + Grafana)

### 1.1 Preparar carpetas en el host

```bash
export DATA_ROOT=/home/tu-usuario/docker   # ajusta a tu caso
mkdir -p $DATA_ROOT/garmin-app/garmin_db
mkdir -p $DATA_ROOT/garmin-app/grafana_data
mkdir -p $DATA_ROOT/garmin-app/backups
```

### 1.2 Autenticación con Garmin

Login inicial con 2FA, guardando los tokens para que no haya que repetirlo en un futuro:

```bash
docker run -it --rm \
  -v "$DATA_ROOT/garmin-app/garmin_db:/app/data" \
  python:3.12-slim \
  bash -c "pip install --quiet garminconnect==0.3.16 && python3 -c \"
from garminconnect import Garmin
import getpass

def get_mfa():
    return input('\n>>> Introduce el codigo de 6 digitos enviado a tu correo: ')

email = input('Email de Garmin: ')
password = getpass.getpass('Contraseña de Garmin: ')

try:
    api = Garmin(email, password, prompt_mfa=get_mfa)
    api.login('/app/data/tokens')
    print('\n¡Autenticación exitosa! Tokens guardados.')
except Exception as e:
    print(f'\nError durante el login: {e}')
\""
```

> **Nota:** Garmin/Cloudflare aplican un rate-limiting muy agresivo a los logins repetidos desde la misma IP. Si ves errores 429, espera 15-30 min o cambia de IP . Una vez generados los tokens, se refrescan solos indefinidamente, no vuelve a hacer falta.

### 1.3 Migración inicial del histórico

```bash
docker run -it --rm \
  -v "$DATA_ROOT/garmin-app/garmin_db:/app/data" \
  python:3.12-slim \
  bash -c "pip install --quiet garminconnect==0.3.16 && python3 /app/data/migrar.py"
```

*(Copia antes `garmin-sync/migrar.py` a esa misma carpeta en el host.)*

### 1.4 Desplegar el stack recurrente

Copia `garmin-sync/sync.py` a `$DATA_ROOT/garmin-app/garmin_db/`. Luego, desde Portainer: **Stacks → Add stack**, pega el contenido de `garmin-sync/docker-compose.yml` y define las variables de entorno del stack (`DATA_ROOT`, `GRAFANA_PASSWORD` — ver `.env.example`). **Deploy the stack**.

Accede a Grafana en `http://<tu-host>:3000` e importa `grafana/dashboard.json` (*Dashboards → New → Import*) o sigue `grafana/README.md` para montar los paneles a mano.

### 1.5 Sincronización bajo demanda

Permite pedirle a Hermes "sincroniza mis datos ahora" sin esperar a la hora programada ni conectarse por SSH al host. Se usa un servicio HTTP mínimo (`webhook.py`) que solo sabe ejecutar `sync.py`y evitar darle a Hermes acceso al socket de Docker del host.

1. Copia `garmin-sync/webhook.py` a `$DATA_ROOT/garmin-app/garmin_db/`.
2. Genera un token: `openssl rand -hex 32`.
3. Defínelo como variable de entorno `SYNC_WEBHOOK_TOKEN` del stack en Portainer. El servicio `garmin-sync-webhook` ya está en `garmin-sync/docker-compose.yml`; si no lo quieres, bórralo del compose.
4. Comprueba el arranque y funcionamineto:

```bash
docker logs garmin-sync-webhook --tail 10
curl -X POST http://127.0.0.1:8099/sync -H "X-Webhook-Token: token-invalido" -w "\nHTTP status: %{http_code}\n"   # debe dar 401
curl -X POST http://127.0.0.1:8099/sync -H "X-Webhook-Token: TU_TOKEN" -w "\nHTTP status: %{http_code}\n"        # debe dar 200
```

5. Si usas Hermes, guarda el mismo token donde él pueda leerlo, siempre **fuera de las skills**:

```bash
mkdir -p $DATA_ROOT/hermes/data/secrets
echo -n "TU_TOKEN" > $DATA_ROOT/hermes/data/secrets/webhook_token.txt
chmod 600 $DATA_ROOT/hermes/data/secrets/webhook_token.txt
```

El puerto se publica solo en `127.0.0.1` del host. Hermes corre con `network_mode: host`, así que llega a él; desde la red local no es accesible.

## 2. Opcional: Importar histórico de Apple Watch

Solo si vienes de ese dispositivo. Ver cabecera de `apple-watch-import/importar_apple.py` para el procedimiento completo (exportar desde Salud, transferir, ajustar fechas, ejecutar). Es una importación puntual para volcar todos los datos.

## 3. Hermes Agent — coach por Telegram

### 3.1 Construir la imagen

Hermes Agent es un proyecto de terceros (Nous Research), se construye desde el código fuente oficial:

```bash
git clone https://github.com/NousResearch/hermes-agent.git
cd hermes-agent
docker build -t hermes-agent:local .
```

### 3.2 Desplegar

Prepara la carpeta de datos y despliega vía Portainer con `hermes/docker-compose.yml` (variables: `DATA_ROOT`, `HERMES_UID`, `HERMES_GID` — averigua estas dos últimas con `id -u`/`id -g` de tu usuario del host):

```bash
mkdir -p $DATA_ROOT/hermes/data
```

### 3.3 Configurar modelo, Telegram y personalidad

Dentro del contenedor ya desplegado:

```bash
docker exec -it hermes hermes model           # selecciona el modelo de IA que quieras (Google, Claude, etc)
docker exec -it hermes hermes fallback add    # modelo de respaldo que decidas
docker exec -it hermes hermes gateway setup   # token del bot (@BotFather) + tu ID de usuario (@userinfobot)
docker restart hermes
```

Copia `hermes/SOUL.md` a `$DATA_ROOT/hermes/data/SOUL.md` y personalízalo.

### 3.4 Skills

Copia las carpetas de `hermes/skills/` a `$DATA_ROOT/hermes/data/skills/`. Los archivos se leen en caliente, sin reiniciar el contenedor, **pero Hermes decide qué skills cargar al empezar cada conversación**: si editas una skill con un chat ya abierto, envía la opcion `/new` por Telegram para que la recargue.

### 3.5 Opcional: Resumen semanal automático

**Diseño: un script hace las cuentas, el modelo solo redacta.** Una versión anterior dejaba que el modelo consultara la base y calculara las medias, y solía dar datos erróneos; además, Hermes **bloquea la ejecución de código en cron** (`execute_code` y `terminal -c` se rechazan porque no hay nadie para aprobarlos), incluso con la aprobación en `always`. Por eso `hermes/scripts/resumen_semanal.py` calcula todo y Hermes inyecta su salida en el prompt (`--script`). El script abre la base en solo lectura, analiza la semana lunes-domingo (en domingo, la semana en curso cuenta como completa), la compara con la anterior y con la media de las 3 previas (ritmo solo de carreras en calle, FC ponderada por duración, ritmos en m:ss) y extrae el patrón real de días de carrera y de fuerza.

```bash
mkdir -p $DATA_ROOT/hermes/data/scripts
cp hermes/scripts/resumen_semanal.py $DATA_ROOT/hermes/data/scripts/

# Prueba manual (RESUMEN_HOY simula otra fecha); compara las cifras con Garmin Connect
docker exec -e RESUMEN_HOY=2026-10-25 hermes python3 /opt/data/scripts/resumen_semanal.py

# Cron
docker exec -it hermes hermes cron add "0 20 * * 0" "Redacta el resumen semanal de carreras a partir ÚNICAMENTE del bloque de datos que se te ha inyectado: ya está calculado; no consultes la base de datos, no ejecutes código y no recalcules nada. Primero, la comparación de la semana analizada con la anterior y con la media de las tres previas (volumen, ritmo y FC), con las cifras del bloque. Para el ritmo usa siempre 'más rápido' o 'más lento', nunca 'por encima' o 'por debajo'. Después, un plan para la semana siguiente que respete el patrón real de días de carrera y de fuerza que indica el bloque: no propongas más días de carrera de los habituales sin decir que es un aumento, ni sesiones exigentes en días de fuerza. Si el bloque dice que no hay objetivo, dilo en una línea y no inventes uno. Escribe solo el mensaje final." --script resumen_semanal.py --name "resumen-dominical" --deliver telegram
```

>**Notas:**
>
> **No fijes `--model` aquí.** Un job con modelo fijado nunca usa el respaldo configurado, y un modelo completo como `gemini-3.8-flash` admite solo 5 peticiones por minuto en el nivel gratuito, así que el job puede fallar en silencio a mitad de tarea. Sin `--model` hereda el modelo por defecto y el respaldo.
>
> **`--deliver telegram` es imprescindible.** El valor por defecto es `local`: el resultado se guarda en disco dentro de Hermes (`/opt/data/cron/output/`) y no se envía a ningún chat. `--failure-deliver local` silencia por completo los avisos de fallo; si lo omites, los fallos siguen a `--deliver`. Prueba cualquier cron nuevo con un job de un solo uso antes de fiarte de él: `hermes cron add "2m" "<prompt>" --script resumen_semanal.py --name prueba --deliver telegram --repeat 1`, y comprueba que las cifras del mensaje coinciden con la salida del script.
>
> **¿Por qué no dejar que calcule el modelo?** Las primeras versiones juntaron dos semanas en una sola ventana, dieron el ritmo en minutos decimales, promediaron las velocidades de cada carrera en vez de usar tiempo total entre distancia total, entre otros errores. Las reglas de la skill ("Cálculos y formato") ayudaron, pero la solución fiable fue sacar la aritmética del modelo. Dentro del contenedor la carpeta de datos es `/opt/data`, no `/data` (solo `garmin.db` está montado en `/data`).


### 3.6 Aprobación para ejecutar código

Hermes pide confirmación explícita (`allow once / session / always / deny`) la primera vez que va a ejecutar código, por ejemplo para generar una gráfica. Si no hay respuesta en 5 minutos no se ejecuta. `always` evita la espera en tus chats, pero **no** desbloquea la ejecución de código en cron, donde Hermes siempre la bloquea como ya se vio. El riesgo de aplicar `always` queda acotado porque el acceso a `garmin.db` sigue en solo lectura.

### 3.7 Bienestar diario (sueño/estrés/VFC/FC reposo)

Requiere la skill `garmin-wellness` ya copiada (paso 3.4). Comprueba primero desde cuándo hay datos reales en tu cuenta antes del backfill (ver cabecera de `migrar_wellness.py`), ajusta `FECHA_INICIO`, y ejecuta igual que `migrar.py`. La sincronización recurrente ya está integrada en `sync.py` (función `sync_wellness`).

## Variables de entorno

| Variable | Dónde se usa | Secreta |
|---|---|---|
| `DATA_ROOT` | Ambos stacks | No |
| `GRAFANA_PASSWORD` | `garmin-sync` | **Sí** |
| `SYNC_WEBHOOK_TOKEN` | `garmin-sync` (webhook opcional) | **Sí** — y el mismo valor en `hermes/data/secrets/webhook_token.txt` |
| `HERMES_UID` / `HERMES_GID` | `hermes` | No |
| API key del modelo de IA principal | Dentro del contenedor (`hermes model`) | **Sí** — nunca en el compose |
| API key del modelo de IA de respaldo | Dentro del contenedor (`hermes fallback add`) | **Sí** — nunca en el compose |
| Token del bot de Telegram | Dentro del contenedor (`hermes gateway setup`) | **Sí** — nunca en el compose |

## Lecciones / gotchas conocidos
- **`garth` no funciona** — `garminconnect` moderno (0.3.x) gestiona su propia autenticación sin depender de él. Requiere Python 3.12+.
- **Para gráficos de barras por fecha en Grafana con el plugin SQLite**: usa el panel "Time series" con estilo "Bars", no el tipo "Bar chart" (pensado para categorías de texto, no fechas).
- **No dar por hecho los valores de una columna a partir de una muestra**: una skill y siete consultas de Grafana asumieron que `activity_type` era siempre `'running'` por mirar una sola actividad, y dejaban fuera trails, ultras y cinta (incluido un ultra de 54 km). Ejecuta `SELECT col, COUNT(*) FROM tabla GROUP BY 1` antes de filtrar por ella.
- **Python sin terminal retiene `print()`**: un servicio de larga duración parece mudo en `docker logs`. `PYTHONUNBUFFERED=1` lo soluciona.
- El parámetro **`sleep 86400` no es "una vez al día a una hora fija"**: cuenta 24h desde que terminó la ejecución anterior, así que cada reinicio del contenedor desajusta la hora real sin control. `garmin-sync/docker-compose.yml` calcula en su lugar la próxima vez que el reloj marque una hora concreta, usando `$$VARIABLE` (dólar duplicado) para las variables del propio `bash` del contenedor — sin el escape, Docker Compose las interpreta como variables suyas (igual que `${DATA_ROOT}`) y las sustituye por una cadena vacía antes de que el comando llegue a ejecutarse. Necesita además `TZ=Europe/Madrid` como variable de entorno: el paquete `tzdata` de `apt` solo aporta la base de datos de zonas horarias, no selecciona cuál usa `date` por defecto.

## Licencia

MIT — ver `LICENSE`. Incluye también la atribución al proyecto de terceros [Hermes Agent](https://github.com/NousResearch/hermes-agent) (Nous Research, MIT), del que este repo depende pero no redistribuye.
