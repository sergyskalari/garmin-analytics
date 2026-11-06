🇪🇸 [Leer en español](README.es.md)

# Garmin Analytics + Hermes Coach

A self-hosted system (designed for a Raspberry Pi, but it runs on any Docker host) that pulls your training and wellness history from Garmin Connect (and optionally from an Apple Watch), stores it in SQLite, visualizes it in Grafana, and exposes it to an AI agent ([Hermes Agent](https://github.com/NousResearch/hermes-agent)) that gives conversational coaching over Telegram — with full access to your history, on-demand charts and an automatic weekly summary.

> **Language note.** This documentation and the code comments are in English. The agent itself is used in Spanish, so its skills (`hermes/skills/`), its personality file (`hermes/SOUL.md`) and the sample chat prompts are in Spanish on purpose: they are operating instructions for the bot, and how a skill is activated depends on its exact wording. Likewise, identifiers, log messages and file names in the scripts (`sync_actividades`, `migrar.py`, ...) are still in Spanish. Database columns are in English.

## ⚠️ Security and privacy — read before deploying

- **This repository contains no personal data, tokens or credentials.** Everything sensitive (Garmin tokens, the `garmin.db` database itself, passwords, API keys) stays on the host where you deploy the project and never goes into the repo — see `.gitignore`.
- Third-party credentials (Gemini, OpenRouter, Telegram bot) are entered interactively **inside the already-running container**, never as plain-text environment variables in any versioned file.
- Before your first `git push`, check that you have not accidentally created a real `.env`, a `garmin_db/` folder with data, or a `hermes/data/` folder with your own configuration inside the repo. `.gitignore` excludes them, but it is worth confirming once (`git status` before the first commit).
- All read-only access (Grafana, Hermes) to `garmin.db` goes through Docker volumes mounted with `:ro` — the lock is enforced by the host filesystem, not by a coding convention. You can verify it yourself by trying to write from inside either container; it must fail.

## Architecture

```
Garmin Connect (unofficial API)          Apple Health (XML export, once — optional)
        │                                           │
        ▼                                           ▼
  sync.py (Docker, once a day)           importar_apple.py (once)
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
                                   Gemini API (free tier)
                                   + OpenRouter fallback
```

## Requirements

- A Docker host (Raspberry Pi 5 or similar; this project was developed and tested on ARM64)
- [Portainer](https://www.portainer.io/) (recommended for managing the stacks through a web UI) or plain `docker compose`
- A Garmin Connect account
- *(Optional)* An Apple Health export, if you are coming from an Apple Watch
- A free account on [Google AI Studio](https://aistudio.google.com) (Hermes' AI provider)
- *(Optional but recommended)* A free [OpenRouter](https://openrouter.ai) account (fallback provider)
- A Telegram bot created through [@BotFather](https://t.me/BotFather)

## Repository layout

```
.
├── README.es.md          # Spanish version of this README
├── .env.example          # environment variable template — copy as .env
├── garmin-sync/           # core: Garmin extractor + Grafana
│   ├── docker-compose.yml
│   ├── sync.py             # recurring sync (activities + wellness)
│   ├── webhook.py          # optional: on-demand sync
│   ├── migrar.py           # initial activity migration (run once)
│   └── migrar_wellness.py  # initial wellness migration (run once, optional)
├── apple-watch-import/    # optional: only if you come from an Apple Watch
│   └── importar_apple.py
├── hermes/                 # optional: conversational coach over Telegram
│   ├── docker-compose.yml
│   ├── SOUL.md              # agent personality template
│   ├── scripts/
│   │   └── resumen_semanal.py  # weekly-summary calculations (cron --script)
│   └── skills/
│       ├── garmin-coach/SKILL.md
│       └── garmin-wellness/SKILL.md
└── grafana/
    ├── dashboard.json     # dashboard exported from Grafana (importable)
    ├── README.md          # SQL queries behind each panel
    └── README.es.md       # Spanish version
```

## 1. Core deployment (extractor + Grafana)

### 1.1 Prepare folders on the host

```bash
export DATA_ROOT=/home/your-user/docker   # adjust to your setup
mkdir -p $DATA_ROOT/garmin-app/garmin_db
mkdir -p $DATA_ROOT/garmin-app/grafana_data
mkdir -p $DATA_ROOT/garmin-app/backups
```

### 1.2 Authenticate with Garmin

Initial login with 2FA, saving the tokens so you never have to repeat it. (The prompts of this one-off snippet are in Spanish; the code works the same.)

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

> **Note:** Garmin/Cloudflare apply aggressive rate limiting to repeated logins from the same IP. If you see 429 errors, wait 15-30 minutes or change IP (e.g. use mobile data for a minute). Once the tokens exist they refresh themselves indefinitely — you will not need to log in again.

### 1.3 Initial history migration

```bash
docker run -it --rm \
  -v "$DATA_ROOT/garmin-app/garmin_db:/app/data" \
  python:3.12-slim \
  bash -c "pip install --quiet garminconnect==0.3.16 && python3 /app/data/migrar.py"
```

*(Copy `garmin-sync/migrar.py` into that same folder on the host first.)*

### 1.4 Deploy the recurring stack

Copy `garmin-sync/sync.py` to `$DATA_ROOT/garmin-app/garmin_db/`. Then, in Portainer: **Stacks → Add stack**, paste the contents of `garmin-sync/docker-compose.yml`, and define the stack's environment variables (`DATA_ROOT`, `GRAFANA_PASSWORD` — see `.env.example`). **Deploy the stack**.

Open Grafana at `http://<your-host>:3000` and import `grafana/dashboard.json` (*Dashboards → New → Import*), or follow `grafana/README.md` to build the panels by hand.

### 1.5 (Optional) On-demand sync

Lets you ask Hermes to "sync my data now" without waiting for the scheduled time or logging in over SSH. It uses a minimal HTTP service (`webhook.py`) that only knows how to run `sync.py`, instead of giving Hermes access to the host's Docker socket (which would amount to control over every container).

1. Copy `garmin-sync/webhook.py` to `$DATA_ROOT/garmin-app/garmin_db/`.
2. Generate a token: `openssl rand -hex 32`.
3. Define it as the stack variable `SYNC_WEBHOOK_TOKEN` in Portainer (not in the YAML). The `garmin-sync-webhook` service is already in `garmin-sync/docker-compose.yml`; delete it from the compose if you do not want it.
4. Check that it starts and that the lock works:

```bash
docker logs garmin-sync-webhook --tail 10
curl -X POST http://127.0.0.1:8099/sync -H "X-Webhook-Token: invalid-token" -w "\nHTTP status: %{http_code}\n"   # must return 401
curl -X POST http://127.0.0.1:8099/sync -H "X-Webhook-Token: YOUR_TOKEN" -w "\nHTTP status: %{http_code}\n"       # must return 200
```

5. If you use Hermes, store the same token where it can read it, **outside the skills**:

```bash
mkdir -p $DATA_ROOT/hermes/data/secrets
echo -n "YOUR_TOKEN" > $DATA_ROOT/hermes/data/secrets/webhook_token.txt
chmod 600 $DATA_ROOT/hermes/data/secrets/webhook_token.txt
```

The port is published only on the host's `127.0.0.1`. Hermes runs with `network_mode: host`, so it can reach it; it is not reachable from the local network.

## 2. (Optional) Import Apple Watch history

Only if you are coming from that device. See the header of `apple-watch-import/importar_apple.py` for the full procedure (export from Health, transfer, adjust the dates, run). It is a one-off import, not a recurring one.

## 3. (Optional) Hermes Agent — coach over Telegram

### 3.1 Build the image

Hermes Agent is a third-party project (Nous Research); its image is not distributed in this repo and is built from the official source code:

```bash
git clone https://github.com/NousResearch/hermes-agent.git
cd hermes-agent
docker build -t hermes-agent:local .
```

### 3.2 Deploy

Prepare the data folder and deploy through Portainer with `hermes/docker-compose.yml` (variables: `DATA_ROOT`, `HERMES_UID`, `HERMES_GID` — find the last two with `id -u` / `id -g` for your host user):

```bash
mkdir -p $DATA_ROOT/hermes/data
```

### 3.3 Configure the model, Telegram and personality

Inside the already-running container:

```bash
docker exec -it hermes hermes model          # pick Google AI Studio, a Flash-Lite model
docker exec -it hermes hermes fallback add    # optional: OpenRouter as a fallback
docker exec -it hermes hermes gateway setup   # bot token (@BotFather) + your user ID (@userinfobot)
docker restart hermes
```

Copy `hermes/SOUL.md` to `$DATA_ROOT/hermes/data/SOUL.md` and customize it.

### 3.4 Skills

Copy the folders in `hermes/skills/` to `$DATA_ROOT/hermes/data/skills/`. The files are read live, without restarting the container, **but Hermes decides which skills to load when a conversation starts**: if you edit a skill while a chat is already open, send `/new` on Telegram so it reloads.

### 3.5 Automatic weekly summary (optional)

**Design: a script does the math, the model only writes the text.** An earlier version let the model query the database and compute the averages itself. It got the numbers wrong, and on top of that Hermes **blocks code execution in cron jobs** (`execute_code` and `terminal -c` are refused because nobody is there to approve them), even with approval set to `always`. So `hermes/scripts/resumen_semanal.py` computes everything and Hermes injects its output into the prompt (`--script`). The script opens the database read-only, analyses the Monday-to-Sunday week (on a Sunday the current week counts as complete), compares it with the previous week and the mean of the 3 before it (pace from road runs only, HR weighted by duration, pace as m:ss) and extracts the real weekly pattern of running and strength days. Its output and the prompt are in Spanish because the agent is used in Spanish.

```bash
mkdir -p $DATA_ROOT/hermes/data/scripts
cp hermes/scripts/resumen_semanal.py $DATA_ROOT/hermes/data/scripts/

# Manual test (RESUMEN_HOY simulates another date); compare the figures with Garmin Connect
docker exec -e RESUMEN_HOY=2026-10-25 hermes python3 /opt/data/scripts/resumen_semanal.py

# Cron job
docker exec -it hermes hermes cron add "0 20 * * 0" "Redacta el resumen semanal de carreras a partir ÚNICAMENTE del bloque de datos que se te ha inyectado: ya está calculado; no consultes la base de datos, no ejecutes código y no recalcules nada. Primero, la comparación de la semana analizada con la anterior y con la media de las tres previas (volumen, ritmo y FC), con las cifras del bloque. Para el ritmo usa siempre 'más rápido' o 'más lento', nunca 'por encima' o 'por debajo'. Después, un plan para la semana siguiente que respete el patrón real de días de carrera y de fuerza que indica el bloque: no propongas más días de carrera de los habituales sin decir que es un aumento, ni sesiones exigentes en días de fuerza. Si el bloque dice que no hay objetivo, dilo en una línea y no inventes uno. Escribe solo el mensaje final." --script resumen_semanal.py --name "resumen-dominical" --deliver telegram
```

> **Do not pin `--model` here.** A job with a pinned model never uses the configured fallback, and a full model such as `gemini-3.8-flash` allows only 5 requests per minute on the free tier, so the job can fail silently halfway through. Without `--model` it inherits the default model and the fallback.
>
> **`--deliver telegram` is essential.** The default is `local`: the result is saved to disk inside Hermes (`/opt/data/cron/output/`) and is not sent to any chat. `--failure-deliver local` silences failure notices entirely; if you omit it, failures follow `--deliver`. Test any new cron with a one-shot job before relying on it: `hermes cron add "2m" "<prompt>" --script resumen_semanal.py --name test --deliver telegram --repeat 1`, and check the figures in the message against the script output.
>
> **Why not let the model calculate?** The first versions merged two weeks into one window, reported pace in decimal minutes (4,77 min/km is really 4:46/km), averaged per-run speeds instead of using total time over total distance, and miscalculated a mean by hand. Rules in the skill ("Cálculos y formato") helped, but the reliable fix was moving the arithmetic out of the model. Inside the container the data folder is `/opt/data`, not `/data` (only `garmin.db` is mounted at `/data`).
>
> **Fallback model.** Free OpenRouter models (`:free`) come and go: one configured fallback started returning `404` weeks later, which made a failed Gemini run unrecoverable. List the current ones with tool support: `curl -s https://openrouter.ai/api/v1/models` and keep those ending in `:free` that have `tools` in `supported_parameters`. Change it with `hermes fallback clear`, `hermes fallback add`, `hermes fallback list`, and review it from time to time.

### 3.6 Approval to run code

Hermes asks for explicit confirmation (`allow once / session / always / deny`) the first time it is about to run code, for example to generate a chart. Without an answer within 5 minutes it does not run. `always` avoids the wait in your chats, but it does **not** unlock code execution in cron jobs, where Hermes always blocks it; that is why the weekly summary uses a script instead. The risk of `always` stays bounded because `garmin.db` remains mounted read-only.

### 3.7 Daily wellness (sleep / stress / HRV / resting HR)

Optional; requires the `garmin-wellness` skill already copied (step 3.4). First check from which date your account has real data before running the backfill (see the header of `migrar_wellness.py`), adjust `FECHA_INICIO` (the start-date variable), and run it the same way as `migrar.py`. Recurring sync is already built into `sync.py` (function `sync_wellness`).

## Environment variables

| Variable | Used by | Secret |
|---|---|---|
| `DATA_ROOT` | Both stacks | No |
| `GRAFANA_PASSWORD` | `garmin-sync` | **Yes** |
| `SYNC_WEBHOOK_TOKEN` | `garmin-sync` (optional webhook) | **Yes** — and the same value in `hermes/data/secrets/webhook_token.txt` |
| `HERMES_UID` / `HERMES_GID` | `hermes` | No |
| Gemini API key | Inside the container (`hermes model`) | **Yes** — never in the compose file |
| OpenRouter API key | Inside the container (`hermes fallback add`) | **Yes** — never in the compose file |
| Telegram bot token | Inside the container (`hermes gateway setup`) | **Yes** — never in the compose file |

## Lessons learned / known gotchas

- **Check with real data before writing schemas or parsers** — APIs that are not officially documented (Garmin, Apple Health) change field names between versions; do not guess them.
- **`garth` is deprecated** — modern `garminconnect` (0.3.x) handles its own authentication without depending on it. It requires Python 3.12+.
- **UID/GID permissions on Docker volumes**: a project's official image may run as a different UID than your host user (e.g. Grafana uses `472`) — check (`id -u`) instead of assuming, and adjust `chown` accordingly if needed.
- **Garmin/Cloudflare rate limiting**: pauses between requests and overnight backfills avoid 429 blocks.
- **An API's free tier is not just "requests per day"**: the per-minute limit can be the real bottleneck for an agent that chains calls (Hermes makes several calls per conversation turn).
- **Bar charts by date in Grafana with the SQLite plugin**: use the "Time series" panel with the "Bars" style, not the "Bar chart" type (designed for text categories, not dates).
- **Do not let the model calculate what a script can**: the weekly summary uses a Python script (`--script`) for all arithmetic and the model only writes the text. It is cheaper, more reliable, and does not depend on code execution, which Hermes blocks in cron. Verify the script's output against the source (Garmin Connect) before trusting it.
- **The per-minute limit may be on tokens, not requests**: free Gemini caps input tokens per minute, and an agent that resends its context on every call exhausts it in a few calls. Check which metric the 429 error names.
- **Free `:free` models on OpenRouter disappear**: a fallback configured weeks ago can return 404 the day you need it. Test it from time to time.
- **Do not oversize the model of a cron "just in case"**: pinning a model with a tighter requests-per-minute limit to guard against a problem that prompt does not have (e.g. generating charts) only introduces a new failure. Also check the delivery target: `--deliver` defaults to `local` (saves to disk, sends nothing), so a cron can run fine and never reach you.
- **Do not infer a column's values from a sample**: a skill and seven Grafana queries assumed `activity_type` was always `'running'` from looking at a single activity, and silently left out trails, ultras and treadmill runs (including a 54 km ultra). Run `SELECT col, COUNT(*) FROM table GROUP BY 1` before filtering on a column.
- **Skills are chosen when the conversation starts**: after editing a skill or its `description`, an open chat does not reload it; send `/new`. The `description` decides whether it loads, so it must name the actions too ("sync"), not just the query topics.
- **Python without a terminal buffers `print()`**: a long-running service looks silent in `docker logs`. `PYTHONUNBUFFERED=1` fixes it.
- **Do not give an agent more access than it needs**: to trigger the sync, the Docker socket (control over the whole host) was ruled out in favor of a webhook that runs a single action, with the port on loopback and the token kept outside the skills.
- **`sleep 86400` is not "once a day at a fixed time"**: it counts 24 hours from the end of the previous run, so every container restart shifts the real time unpredictably. `garmin-sync/docker-compose.yml` instead computes the next time the clock hits a given hour, using `$$VARIABLE` (doubled dollar sign) for the container's own `bash` variables — without the escape, Docker Compose treats them as its own variables (like `${DATA_ROOT}`) and replaces them with an empty string before the command ever runs. It also needs `TZ=Europe/Madrid` (set your own timezone) as an environment variable: the `apt` `tzdata` package only provides the timezone database, it does not choose which zone `date` uses by default.

## License

MIT — see `LICENSE`. It also credits the third-party project [Hermes Agent](https://github.com/NousResearch/hermes-agent) (Nous Research, MIT), which this repo depends on but does not redistribute.
