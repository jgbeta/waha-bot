# Deployment Runbook - WAHA AI WhatsApp Bot

## 1. Runtime Map

| Role | Default URL | Notes |
|---|---|---|
| WAHA | `http://localhost:3000` | WhatsApp session and dashboard |
| Bot | `http://localhost:8000` | FastAPI webhook and worker |
| Bot -> WAHA | `http://waha:3000` | Internal Docker Compose network |
| WAHA -> Bot | `http://bot:8000/webhook` | Internal webhook URL |
| SQLite | `/data/bot.sqlite3` | Default durable jobs, dedupe, and optional chat history |
| PostgreSQL | external host | Optional advanced store when `BOT_STORE=postgres` |

For LAN or remote deployments, replace `localhost` in user-facing commands with the host IP or DNS name. Keep the internal Compose URLs as `http://waha:3000` and `http://bot:8000/webhook`.

## 2. First-Time Setup

```bash
cp .env.example .env
mkdir -p data waha-sessions
```

The `data` and `waha-sessions` directories are host directories used by the Compose bind mounts. They keep SQLite state and WhatsApp session/auth data outside the containers.

Edit `.env` and set at least:

```env
WAHA_API_KEY=<long random key>
WAHA_DASHBOARD_PASSWORD=<dashboard password>
OPENAI_API_KEY=<your OpenAI or compatible key>
BOT_ALLOWED_PHONES=12025550123
BOT_STORE=sqlite
BOT_SQLITE_PATH=/data/bot.sqlite3
BOT_HISTORY_STORE=sqlite
```

`WAHA_WEBHOOK_HMAC_KEY` can stay blank for initial local validation. For real deployments, set it to a shared secret so WAHA signs webhook calls and the bot verifies them.

## 3. Optional PostgreSQL Setup

SQLite is the default. To use PostgreSQL, replace the active SQLite storage block in `.env` with:

```env
BOT_STORE=postgres
BOT_HISTORY_STORE=postgres
PG_HOST=PostgreSQL_IP
PG_PORT=5432
PG_USER=postgres_username
PG_PASSWORD=postgres_password
PG_DBNAME=database_name
PG_TABLE=table_name
PG_CONTACT_NAME=contact_name
```

Then rebuild the image so Docker installs `requirements-postgres.txt`:

```bash
docker compose up -d --build
```

`PG_CONTACT_NAME` is a legacy label written to the Postgres table `usr` column for user messages. It is not the WhatsApp phone ID and does not replace `BOT_ALLOWED_PHONES` or `BOT_ALLOWED_CHAT_IDS`.

The Postgres history table must be compatible with:

```sql
CREATE TABLE IF NOT EXISTS table_name (
    dt TEXT NOT NULL,
    usr TEXT NOT NULL,
    msg TEXT NOT NULL,
    type TEXT NOT NULL,
    model_info TEXT NOT NULL
);
```

The bot also creates and uses `waha_inbound_jobs` for durable inbound job dedupe and retry state.

## 4. Persistent Storage

The default Compose file persists runtime state with bind mounts:

```yaml
services:
  waha:
    volumes:
      - ./waha-sessions:/app/.sessions
  bot:
    volumes:
      - ./data:/data
```

`BOT_SQLITE_PATH=/data/bot.sqlite3` is a path inside the bot container. It is durable only because `./data:/data` maps `/data` to a host directory. To move SQLite storage, change the left side of the volume, for example `/srv/waha-bot/data:/data`, and usually keep `BOT_SQLITE_PATH=/data/bot.sqlite3`.

WAHA stores WhatsApp session/auth data under `/app/.sessions`. Keep `./waha-sessions:/app/.sessions`, or change the left side to another host path such as `/srv/waha-bot/waha-sessions:/app/.sessions`, so QR login survives restarts and rebuilds.

PostgreSQL mode stores jobs/history in PostgreSQL instead of local SQLite, but WAHA still needs the `waha-sessions` mount for persistent WhatsApp authentication.

## 5. Startup

```bash
docker compose up -d --build
docker compose logs -f waha bot
```

Open the WAHA dashboard:

```text
http://localhost:3000/dashboard
```

Start the `default` session and scan the QR code. Confirm status:

```bash
curl -s -H "X-Api-Key: $(grep WAHA_API_KEY .env | cut -d= -f2)" \
  http://localhost:3000/api/sessions/default | python3 -m json.tool
```

Wait for `"status": "WORKING"`.

## 6. Health Checks

```bash
curl http://localhost:3000/ping
curl http://localhost:8000/health
curl http://localhost:8000/ready | python3 -m json.tool
docker compose ps
```

Run the bot with one Uvicorn worker. The default worker uses an in-process queue, per-chat locks, and LID cache.

## 7. Webhook Test

If `WAHA_WEBHOOK_HMAC_KEY` is blank, replay the sample webhook. The sample uses `12025550123@c.us`; keep that number allowlisted for this endpoint-only test, or edit `examples/webhook_message.json` to match one of your allowlisted chat IDs. To avoid sending a reply during replay testing, set `BOT_AUTOREPLY_ENABLED=false` and restart the bot first.

```bash
curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -d @examples/webhook_message.json
```

Expected response:

```json
{"ok": true, "queued": true}
```

Inspect SQLite job state:

```bash
sqlite3 data/bot.sqlite3 \
  "SELECT message_id, chat_id, status, error FROM inbound_jobs ORDER BY received_at DESC LIMIT 5;"
```

For PostgreSQL job state:

```bash
psql -h "$PG_HOST" -U "$PG_USER" -d "$PG_DBNAME" \
  -c "SELECT message_id, chat_id, status, error FROM waha_inbound_jobs ORDER BY received_at DESC LIMIT 5;"
```

## 8. Allowlist And LID Mapping

WAHA may deliver a personal chat as a Linked ID such as `62590675898548@lid` instead of `12025550123@c.us`. The bot resolves inbound `@lid` values through WAHA before applying `BOT_ALLOWED_PHONES`.

Manual mapping check:

```bash
curl -s -H "X-Api-Key: $(grep WAHA_API_KEY .env | cut -d= -f2)" \
  "http://localhost:3000/api/default/lids/62590675898548%40lid" | python3 -m json.tool
```

Expected shape:

```json
{"lid":"62590675898548@lid","pn":"12025550123@c.us"}
```

If the mapping is missing or points to another phone, the bot logs `ignored_disallowed_chat` and does not reply.

## 9. Storage Modes

Recommended SQLite default:

```env
BOT_STORE=sqlite
BOT_HISTORY_STORE=sqlite
```

PostgreSQL mode:

```env
BOT_STORE=postgres
BOT_HISTORY_STORE=postgres
```

Privacy mode with SQLite jobs but no conversation history:

```env
BOT_STORE=sqlite
BOT_HISTORY_STORE=none
BOT_RETAIN_PROCESSED_MESSAGE_BODY=false
```

Testing-only mode:

```env
BOT_STORE=memory
BOT_HISTORY_STORE=memory
BOT_REQUIRE_ALLOWLIST=false
```

## 10. Debug Modes

```env
BOT_AUTOREPLY_ENABLED=false  # accept and store webhooks, but do not generate/send replies
BOT_DRY_RUN=true             # generate replies, log them, but do not send through WAHA
```

Local prompt test:

```bash
python -m scripts.chat_once "hello"
```

Replay an example webhook:

```bash
python -m scripts.replay_webhook examples/webhook_message.json http://localhost:8000/webhook
```

Inspect SQLite:

```bash
python -m scripts.inspect_db data/bot.sqlite3
```

## 11. Recovery

```bash
docker compose ps
docker compose logs --tail=100 bot
docker compose logs --tail=100 waha
curl http://localhost:8000/health
curl http://localhost:8000/ready | python3 -m json.tool
```

If WAHA logs `getaddrinfo ENOTFOUND bot` or `connect ECONNREFUSED ...:8000`, WAHA cannot reach the Compose service named `bot`. This is Docker DNS, startup order, stale-container, or bot-startup failure. It is not caused by WhatsApp authentication, OpenAI, or a Postgres query inside message handling.

First reset stale containers and orphans, then rebuild:

```bash
docker compose down --remove-orphans
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 bot
```

After WAHA is running, verify Docker DNS from inside the WAHA container:

```bash
docker compose exec waha node -e "require('dns').lookup('bot',(e,a)=>{console.log(e||a);process.exit(e?1:0)})"
```

If the DNS lookup fails, confirm you started both services from the same directory and Compose project. If `bot` is missing or unhealthy, fix the bot logs first.

For PostgreSQL mode, common bot-startup causes are:

- `BOT_STORE=postgres` but `BOT_HISTORY_STORE` left unset, which defaults to SQLite and is invalid.
- `.env` changed to `BOT_STORE=postgres` but the image was not rebuilt with `docker compose up -d --build`.
- Postgres connection settings are blank or wrong.
- `PG_TABLE` exists but is missing one of `dt`, `usr`, `msg`, `type`, or `model_info`.

If WAHA is `WORKING` but no reply is sent, check bot logs for:

- `ignored_disallowed_chat`: allowlist or LID mapping issue.
- `openai_failed`: model, API key, or provider issue.
- `waha_post_failed`: WAHA send API issue.
- queued rows in the selected store: worker did not process or reply failed.
