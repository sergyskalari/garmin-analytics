🇬🇧 [Read this in English](README.md)

# Garmin Analytics + Hermes Coach

Sistema autoalojado (pensado para una Raspberry Pi, pero corre en cualquier host Docker) que extrae tu historial de entrenamiento y bienestar desde Garmin Connect (y opcionalmente Apple Watch), lo guarda en SQLite, lo visualiza en Grafana, y lo pone a disposición de un agente de IA ([Hermes Agent](https://github.com/NousResearch/hermes-agent)) que da coaching conversacional por Telegram — con memoria completa del historial, gráficas bajo demanda y un resumen semanal automático.

## ⚠️ Seguridad y privacidad — léelo antes de desplegar

- **Este repositorio no contiene ningún dato personal, token ni credencial.** Todo lo sensible (tokens de Garmin, la propia base de datos `garmin.db`, contraseñas, API keys) se queda en el host donde despliegues el proyecto, nunca en el repo — ver `.gitignore`.
- Las credenciales de terceros (Gemini, OpenRouter, bot de Telegram) se introducen de forma interactiva **dentro del contenedor ya desplegado**, no como variables de entorno en texto plano en ningún archivo versionado.
- Antes de hacer tu primer `git push`, revisa que no hayas creado sin querer un `.env` real, una carpeta `garmin_db/` con datos, o un `hermes/data/` con tu propia configuración dentro del repo — el `.gitignore` los excluye, pero conviene comprobarlo una vez (`git status` antes del primer commit).
- Todo acceso de solo lectura (Grafana, Hermes) a `garmin.db` se hace vía volúmenes Docker montados en modo `:ro` — el candado es del sistema de ficheros del host, no una convención de código. Puedes verificarlo tú mismo intentando escribir desde dentro de cualquiera de esos contenedores; debe fallar.

## Arquitectura

```
Garmin Connect (API no oficial)          Apple Health (export XML, una vez — opcional)
        │                                           │
        ▼                                           ▼
  sync.py (Docker, 1 vez/día)           importar_apple.py (una vez)
        │                                           │
        └───────────────────┬───────────────────────┘
                             ▼
                    garmin.db (SQLite)
                   activities · activity_laps · daily_wellness
                             │
              ┌──────────────┴──────────────┐
              ▼                              ▼
          Grafana                      Hermes Agent
   (dashboards, :ro)              (Telegram, :ro + skills)
                                          │
                                   Gemini API (gratis)
                                   + fallback OpenRouter
```

## Requisitos

- Host con Docker (Raspberry Pi 5 u otro; este proyecto se desarrolló y probó en ARM64)
- [Portainer](https://www.portainer.io/) (recomendado para gestionar los stacks vía interfaz web) o `docker compose` directamente
- Cuenta de Garmin Connect
- *(Opcional)* Export de Apple Health, si vienes de Apple Watch
- Cuenta gratuita en [Google AI Studio](https://aistudio.google.com) (proveedor de IA de Hermes)
- *(Opcional pero recomendado)* Cuenta gratuita en [OpenRouter](https://openrouter.ai) (proveedor de respaldo)
- Bot de Telegram creado vía [@BotFather](https://t.me/BotFather)

## Estructura del repositorio

```
.
├── README.md             # versión en inglés de este README
├── .env.example          # plantilla de variables de entorno — copiar como .env
├── garmin-sync/           # núcleo: extractor Garmin + Grafana
│   ├── docker-compose.yml
│   ├── sync.py             # sincronización recurrente (actividades + bienestar)
│   ├── webhook.py          # opcional: sincronización bajo demanda
│   ├── migrar.py           # migración inicial de actividades (una vez)
│   └── migrar_wellness.py  # migración inicial de bienestar (una vez, opcional)
├── apple-watch-import/    # opcional: solo si vienes de Apple Watch
│   └── importar_apple.py
├── hermes/                 # opcional: coach conversacional por Telegram
│   ├── docker-compose.yml
│   ├── SOUL.md              # plantilla de personalidad del agente
│   ├── scripts/
│   │   └── resumen_semanal.py  # cálculos del resumen semanal (cron --script)
│   └── skills/
│       ├── garmin-coach/SKILL.md
│       └── garmin-wellness/SKILL.md
└── grafana/
    ├── dashboard.json     # dashboard exportado de Grafana (importable)
    ├── README.md          # versión en inglés
    └── README.es.md       # queries SQL de cada panel
```

## 1. Despliegue del núcleo (extractor + Grafana)

### 1.1 Preparar carpetas en el host

```bash
export DATA_ROOT=/home/tu-usuario/docker   # ajusta a tu caso
mkdir -p $DATA_ROOT/garmin-app/garmin_db
mkdir -p $DATA_ROOT/garmin-app/grafana_data
mkdir -p $DATA_ROOT/garmin-app/backups
```

### 1.2 Autenticación con Garmin

Login inicial con 2FA, guardando los tokens para que no haya que repetirlo:

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

> **Nota:** Garmin/Cloudflare aplican rate-limiting agresivo a logins repetidos desde la misma IP. Si ves errores 429, espera 15-30 min o cambia de IP (p. ej. datos móviles un minuto). Una vez generados los tokens, se refrescan solos indefinidamente — no vuelve a hacer falta.

### 1.3 Migración inicial del histórico

```bash
docker run -it --rm \
  -v "$DATA_ROOT/garmin-app/garmin_db:/app/data" \
  python:3.12-slim \
  bash -c "pip install --quiet garminconnect==0.3.16 && python3 /app/data/migrar.py"
```

*(Copia antes `garmin-sync/migrar.py` a esa misma carpeta en el host.)*

### 1.4 Desplegar el stack recurrente

Copia `garmin-sync/sync.py` a `$DATA_ROOT/garmin-app/garmin_db/`. Luego, con Portainer: **Stacks → Add stack**, pega el contenido de `garmin-sync/docker-compose.yml`, y define las variables de entorno del stack (`DATA_ROOT`, `GRAFANA_PASSWORD` — ver `.env.example`). **Deploy the stack**.

Accede a Grafana en `http://<tu-host>:3000` y importa `grafana/dashboard.json` (*Dashboards → New → Import*) o sigue `grafana/README.md` para montar los paneles a mano.

### 1.5 (Opcional) Sincronización bajo demanda

Permite pedirle a Hermes "sincroniza mis datos ahora" sin esperar a la hora programada ni entrar por SSH. Se usa un servicio HTTP mínimo (`webhook.py`) que solo sabe ejecutar `sync.py`, en vez de darle a Hermes acceso al socket de Docker del host (que equivaldría a control sobre cualquier contenedor).

1. Copia `garmin-sync/webhook.py` a `$DATA_ROOT/garmin-app/garmin_db/`.
2. Genera un token: `openssl rand -hex 32`.
3. Defínelo como variable `SYNC_WEBHOOK_TOKEN` del stack en Portainer (no en el YAML). El servicio `garmin-sync-webhook` ya está en `garmin-sync/docker-compose.yml`; si no lo quieres, bórralo del compose.
4. Comprueba el arranque y que el candado funciona:

```bash
docker logs garmin-sync-webhook --tail 10
curl -X POST http://127.0.0.1:8099/sync -H "X-Webhook-Token: token-invalido" -w "\nHTTP status: %{http_code}\n"   # debe dar 401
curl -X POST http://127.0.0.1:8099/sync -H "X-Webhook-Token: TU_TOKEN" -w "\nHTTP status: %{http_code}\n"        # debe dar 200
```

5. Si usas Hermes, guarda el mismo token donde él pueda leerlo, **fuera de las skills**:

```bash
mkdir -p $DATA_ROOT/hermes/data/secrets
echo -n "TU_TOKEN" > $DATA_ROOT/hermes/data/secrets/webhook_token.txt
chmod 600 $DATA_ROOT/hermes/data/secrets/webhook_token.txt
```

El puerto se publica solo en `127.0.0.1` del host. Hermes corre con `network_mode: host`, así que llega a él; desde la red local no es accesible.

## 2. (Opcional) Importar histórico de Apple Watch

Solo si vienes de ese dispositivo. Ver cabecera de `apple-watch-import/importar_apple.py` para el procedimiento completo (exportar desde Salud, transferir, ajustar fechas, ejecutar). Es una importación puntual, no recurrente.

## 3. (Opcional) Hermes Agent — coach por Telegram

### 3.1 Construir la imagen

Hermes Agent es un proyecto de terceros (Nous Research); su imagen no se distribuye en este repo, se construye desde el código fuente oficial:

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
docker exec -it hermes hermes model          # selecciona Google AI Studio, modelo Flash-Lite
docker exec -it hermes hermes fallback add    # opcional: OpenRouter como respaldo
docker exec -it hermes hermes gateway setup   # token del bot (@BotFather) + tu ID de usuario (@userinfobot)
docker restart hermes
```

Copia `hermes/SOUL.md` a `$DATA_ROOT/hermes/data/SOUL.md` y personalízalo.

### 3.4 Skills

Copia las carpetas de `hermes/skills/` a `$DATA_ROOT/hermes/data/skills/`. Los archivos se leen en caliente, sin reiniciar el contenedor, **pero Hermes decide qué skills cargar al empezar cada conversación**: si editas una skill con un chat ya abierto, envía `/new` por Telegram para que la recargue.

### 3.5 Resumen semanal automático (opcional)

**Diseño: un script hace las cuentas, el modelo solo redacta.** Una versión anterior dejaba que el modelo consultara la base y calculara las medias, y se equivocaba; además, Hermes **bloquea la ejecución de código en cron** (`execute_code` y `terminal -c` se rechazan porque no hay nadie para aprobarlos), incluso con la aprobación en `always`. Por eso `hermes/scripts/resumen_semanal.py` calcula todo y Hermes inyecta su salida en el prompt (`--script`). El script abre la base en solo lectura, analiza la semana lunes-domingo (en domingo, la semana en curso cuenta como completa), la compara con la anterior y con la media de las 3 previas (ritmo solo de carreras en calle, FC ponderada por duración, ritmos en m:ss) y extrae el patrón real de días de carrera y de fuerza.

```bash
mkdir -p $DATA_ROOT/hermes/data/scripts
cp hermes/scripts/resumen_semanal.py $DATA_ROOT/hermes/data/scripts/

# Prueba manual (RESUMEN_HOY simula otra fecha); compara las cifras con Garmin Connect
docker exec -e RESUMEN_HOY=2026-10-25 hermes python3 /opt/data/scripts/resumen_semanal.py

# Cron
docker exec -it hermes hermes cron add "0 20 * * 0" "Redacta el resumen semanal de carreras a partir ÚNICAMENTE del bloque de datos que se te ha inyectado: ya está calculado; no consultes la base de datos, no ejecutes código y no recalcules nada. Primero, la comparación de la semana analizada con la anterior y con la media de las tres previas (volumen, ritmo y FC), con las cifras del bloque. Para el ritmo usa siempre 'más rápido' o 'más lento', nunca 'por encima' o 'por debajo'. Después, un plan para la semana siguiente que respete el patrón real de días de carrera y de fuerza que indica el bloque: no propongas más días de carrera de los habituales sin decir que es un aumento, ni sesiones exigentes en días de fuerza. Si el bloque dice que no hay objetivo, dilo en una línea y no inventes uno. Escribe solo el mensaje final." --script resumen_semanal.py --name "resumen-dominical" --deliver telegram
```

> **No fijes `--model` aquí.** Un job con modelo fijado nunca usa el respaldo configurado, y un modelo completo como `gemini-3.8-flash` admite solo 5 peticiones por minuto en el nivel gratuito, así que el job puede fallar en silencio a mitad de tarea. Sin `--model` hereda el modelo por defecto y el respaldo.
>
> **`--deliver telegram` es imprescindible.** El valor por defecto es `local`: el resultado se guarda en disco dentro de Hermes (`/opt/data/cron/output/`) y no se envía a ningún chat. `--failure-deliver local` silencia por completo los avisos de fallo; si lo omites, los fallos siguen a `--deliver`. Prueba cualquier cron nuevo con un job de un solo uso antes de fiarte de él: `hermes cron add "2m" "<prompt>" --script resumen_semanal.py --name prueba --deliver telegram --repeat 1`, y comprueba que las cifras del mensaje coinciden con la salida del script.
>
> **¿Por qué no dejar que calcule el modelo?** Las primeras versiones juntaron dos semanas en una sola ventana, dieron el ritmo en minutos decimales (4,77 min/km son en realidad 4:46/km), promediaron las velocidades de cada carrera en vez de usar tiempo total entre distancia total y calcularon mal una media a mano. Las reglas de la skill ("Cálculos y formato") ayudaron, pero la solución fiable fue sacar la aritmética del modelo. Dentro del contenedor la carpeta de datos es `/opt/data`, no `/data` (solo `garmin.db` está montado en `/data`).
>
> **Modelo de respaldo.** Los modelos gratuitos de OpenRouter (`:free`) aparecen y desaparecen: uno configurado como respaldo empezó a devolver `404` semanas después, y con él un fallo de Gemini ya no tenía salida. Lista los actuales con soporte de herramientas: `curl -s https://openrouter.ai/api/v1/models` y quédate con los que acaban en `:free` y llevan `tools` en `supported_parameters`. Cámbialo con `hermes fallback clear`, `hermes fallback add` y `hermes fallback list`, y revísalo de vez en cuando.

### 3.6 Aprobación para ejecutar código

Hermes pide confirmación explícita (`allow once / session / always / deny`) la primera vez que va a ejecutar código, por ejemplo para generar una gráfica. Sin respuesta en 5 minutos no se ejecuta. `always` evita la espera en tus chats, pero **no** desbloquea la ejecución de código en cron, donde Hermes siempre la bloquea; por eso el resumen semanal usa un script. El riesgo de `always` queda acotado porque `garmin.db` sigue montada en solo lectura.

### 3.7 Bienestar diario (sueño/estrés/VFC/FC reposo)

Opcional, requiere la skill `garmin-wellness` ya copiada (paso 3.4). Comprueba primero desde cuándo hay datos reales en tu cuenta antes del backfill (ver cabecera de `migrar_wellness.py`), ajusta `FECHA_INICIO`, y ejecuta igual que `migrar.py`. La sincronización recurrente ya está integrada en `sync.py` (función `sync_wellness`).

## Variables de entorno

| Variable | Dónde se usa | Secreta |
|---|---|---|
| `DATA_ROOT` | Ambos stacks | No |
| `GRAFANA_PASSWORD` | `garmin-sync` | **Sí** |
| `SYNC_WEBHOOK_TOKEN` | `garmin-sync` (webhook opcional) | **Sí** — y el mismo valor en `hermes/data/secrets/webhook_token.txt` |
| `HERMES_UID` / `HERMES_GID` | `hermes` | No |
| API key de Gemini | Dentro del contenedor (`hermes model`) | **Sí** — nunca en el compose |
| API key de OpenRouter | Dentro del contenedor (`hermes fallback add`) | **Sí** — nunca en el compose |
| Token del bot de Telegram | Dentro del contenedor (`hermes gateway setup`) | **Sí** — nunca en el compose |

## Lecciones / gotchas conocidos

- **Verifica con datos reales antes de escribir esquemas o parsers** — APIs no documentadas oficialmente (Garmin, Apple Health) cambian nombres de campo entre versiones; no se adivinan.
- **`garth` está deprecado** — `garminconnect` moderno (0.3.x) gestiona su propia autenticación sin depender de él. Requiere Python 3.12+.
- **Permisos UID/GID en volúmenes Docker**: la imagen oficial de un proyecto puede correr con un UID distinto al de tu usuario del host (p. ej. Grafana usa `472`) — compruébalo (`id -u`) en vez de asumirlo, y ajusta `chown` en consecuencia si hace falta.
- **Rate-limiting de Garmin/Cloudflare**: pausas entre peticiones y backfills nocturnos evitan bloqueos 429.
- **El nivel gratuito de una API no es solo "peticiones al día"**: el límite por minuto puede ser el cuello de botella real en un agente con llamadas encadenadas (Hermes hace varias llamadas por turno de conversación).
- **Para gráficos de barras por fecha en Grafana con el plugin SQLite**: usa el panel "Time series" con estilo "Bars", no el tipo "Bar chart" (pensado para categorías de texto, no fechas).
- **No dejes que el modelo calcule lo que puede calcular un script**: el resumen semanal usa un script en Python (`--script`) para toda la aritmética y el modelo solo redacta. Es más barato, más fiable y no depende de ejecutar código, que Hermes bloquea en cron. Verifica la salida del script contra la fuente (Garmin Connect) antes de fiarte.
- **El límite por minuto puede ser de tokens, no de peticiones**: Gemini gratuito limita los tokens de entrada por minuto, y un agente que reenvía su contexto en cada llamada lo agota con pocas llamadas. Mira qué métrica cita el error 429.
- **Los modelos `:free` de OpenRouter desaparecen**: un respaldo configurado hace semanas puede devolver 404 el día que lo necesitas. Pruébalo de vez en cuando.
- **No sobredimensiones el modelo de un cron "por si acaso"**: fijar un modelo con límite de RPM más estrecho para protegerte de un problema que ese prompt concreto no tiene (p. ej. generar gráficas) solo introduce un fallo nuevo. Comprueba además el destino de entrega: `--deliver` es `local` por defecto (guarda en disco, no envía nada), así que un cron puede ejecutarse bien y no llegarte nunca.
- **No des por hecho los valores de una columna a partir de una muestra**: una skill y siete consultas de Grafana asumieron que `activity_type` era siempre `'running'` por mirar una sola actividad, y dejaban fuera trails, ultras y cinta (incluido un ultra de 54 km). Ejecuta `SELECT col, COUNT(*) FROM tabla GROUP BY 1` antes de filtrar por ella.
- **Las skills se eligen al empezar la conversación**: tras editar una skill o su `description`, el chat abierto no la recarga; hay que enviar `/new`. El `description` decide si se carga, así que debe nombrar también las acciones ("sincronizar"), no solo los temas de consulta.
- **Python sin terminal retiene `print()`**: un servicio de larga duración parece mudo en `docker logs`. `PYTHONUNBUFFERED=1` lo soluciona.
- **No des a un agente más acceso del necesario**: para lanzar la sincronización se descartó el socket de Docker (control sobre todo el host) a favor de un webhook que solo ejecuta una acción, con puerto en loopback y token fuera de las skills.
- **`sleep 86400` no es "una vez al día a una hora fija"**: cuenta 24h desde que terminó la ejecución anterior, así que cada reinicio del contenedor desajusta la hora real sin control. `garmin-sync/docker-compose.yml` calcula en su lugar la próxima vez que el reloj marque una hora concreta, usando `$$VARIABLE` (dólar duplicado) para las variables del propio `bash` del contenedor — sin el escape, Docker Compose las interpreta como variables suyas (igual que `${DATA_ROOT}`) y las sustituye por una cadena vacía antes de que el comando llegue a ejecutarse. Necesita además `TZ=Europe/Madrid` como variable de entorno: el paquete `tzdata` de `apt` solo aporta la base de datos de zonas horarias, no selecciona cuál usa `date` por defecto.

## Licencia

MIT — ver `LICENSE`. Incluye también la atribución al proyecto de terceros [Hermes Agent](https://github.com/NousResearch/hermes-agent) (Nous Research, MIT), del que este repo depende pero no redistribuye.
