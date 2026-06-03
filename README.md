# WAHA AI WhatsApp Bot

A single-process FastAPI bot for WAHA webhooks. WAHA receives WhatsApp messages, posts them to `/webhook`, and the bot replies with an OpenAI-compatible chat model.

## Quickstart

```bash
cp .env.example .env
mkdir -p data waha-sessions
```

Edit `.env` and set real values for:

```env
WAHA_API_KEY=...
WAHA_DASHBOARD_PASSWORD=...
OPENAI_API_KEY=...
BOT_ALLOWED_PHONES=12025550123
```

Then start the stack:

```bash
docker compose up -d --build
```

Open `http://localhost:3000/dashboard`, start the `default` session, and scan the QR code. Check the bot at `http://localhost:8000/health` and `http://localhost:8000/ready`.

## Defaults

- One runtime: `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
- SQLite is the default persistent store: `/data/bot.sqlite3`.
- PostgreSQL is optional and installed in the Docker image only when `BOT_STORE=postgres` is set before rebuild.
- Allowlists are required by default through `BOT_ALLOWED_PHONES` or `BOT_ALLOWED_CHAT_IDS`.
- Group chats are ignored unless `BOT_ALLOW_GROUPS=true`.
- WAHA `@lid` chat IDs are resolved before allowlist checks.

## Persistent Storage

The default Compose file uses bind mounts so important state survives container rebuilds and recreations:

```yaml
services:
  waha:
    volumes:
      - ./waha-sessions:/app/.sessions
  bot:
    volumes:
      - ./data:/data
```

`BOT_SQLITE_PATH=/data/bot.sqlite3` points inside the bot container. It is persistent because `./data:/data` maps that container path to a host directory. If you remove the mount, SQLite jobs, dedupe state, and chat history can be lost when the container is recreated.

`./waha-sessions:/app/.sessions` keeps WAHA WhatsApp session/auth data on the host, so QR login survives restarts and rebuilds.

To store data somewhere else, change the left side of each volume and keep the right side unchanged:

```yaml
services:
  waha:
    volumes:
      - /srv/waha-bot/waha-sessions:/app/.sessions
  bot:
    volumes:
      - /srv/waha-bot/data:/data
```

For SQLite, usually leave `BOT_SQLITE_PATH=/data/bot.sqlite3` and move the host directory through the Compose volume.

## Storage Modes

Use SQLite for normal personal or family bots:

```env
BOT_STORE=sqlite
BOT_SQLITE_PATH=/data/bot.sqlite3
BOT_HISTORY_STORE=sqlite
```

Use PostgreSQL if you already operate a database and want the legacy `dt/usr/msg/type/model_info` history table:

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

After changing to or from `BOT_STORE=postgres`, rebuild the image:

```bash
docker compose up -d --build
```

`PG_CONTACT_NAME` is a legacy label written to the Postgres table `usr` column for user messages. It is not the WhatsApp phone ID and does not replace `BOT_ALLOWED_PHONES` or `BOT_ALLOWED_CHAT_IDS`.

Disable conversation history while preserving queued unanswered jobs:

```env
BOT_STORE=sqlite
BOT_HISTORY_STORE=none
BOT_RETAIN_PROCESSED_MESSAGE_BODY=false
```

Use memory only for tests or demos:

```env
BOT_STORE=memory
BOT_HISTORY_STORE=memory
BOT_REQUIRE_ALLOWLIST=false
```

## Local Validation

```bash
python -m compileall -q app scripts tests
python -m pytest -q
python -m scripts.replay_webhook examples/webhook_message.json http://localhost:8000/webhook
python -m scripts.inspect_db data/bot.sqlite3
```

If WAHA logs `getaddrinfo ENOTFOUND bot` or `connect ECONNREFUSED ...:8000`, it is a Docker DNS/startup-order problem: WAHA cannot reach the Compose service named `bot`. Start with:

```bash
docker compose ps
docker compose logs --tail=100 bot
curl http://localhost:8000/health
docker compose exec waha node -e "require('dns').lookup('bot',(e,a)=>{console.log(e||a);process.exit(e?1:0)})"
```

If stale containers are involved, reset the Compose project:

```bash
docker compose down --remove-orphans
docker compose up -d --build
```

If you use a LAN or remote host, replace `localhost` with that host name or IP address. See `RUNBOOK.md` for deployment and troubleshooting details.
