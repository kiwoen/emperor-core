# Emperor-Core API Relay (New API)

This pack deploys the **New API** relay (`calciumion/new-api`) — an
OpenAI-compatible gateway that lets emperor-core route *every* model
(OpenAI / DeepSeek / Claude / Gemini / …) through **one URL and one key**,
while the relay handles provider/format conversion, key & quota management,
and full call logging (which becomes your distillation data source).

> Image: `calciumion/new-api:latest` · Port: `3000` · Data: `./data:/data`
> (SQLite is the default when `SQL_DSN` is unset; the `/data` volume must be
>  mounted for persistence.)

---

## 1. Deploy the relay

```bash
cd deploy/relay
cp .env.example .env
# edit .env and set a strong SESSION_SECRET (e.g. `openssl rand -hex 32`)
docker compose up -d
```

The service exposes the OpenAI-compatible API at:

```
http://<host>:3000/v1
```

## 2. Create the root admin

Open **http://\<host>:3000** in a browser. On first launch the web panel
prompts you to **create the root admin account** (there is no env token — you
set the username/password interactively). Log in.

## 3. Add provider channels

In the panel, add **渠道 / Channels** for each provider you want, pasting that
provider's API key (OpenAI, DeepSeek, Claude/Anthropic, Gemini, …). New API
normalizes all of them behind its OpenAI-compatible `/v1` endpoint.

## 4. Create an API key (token)

In the panel, create a **令牌 / API Key (token)**. This single token is what
emperor-core will use to talk to the relay.

## 5. Point emperor-core at the relay

In emperor-core's `.env`:

```bash
EMPEROR_RELAY_URL=http://<host>:3000/v1
EMPEROR_RELAY_KEY=<the token you just created>
```

> `EMPEROR_RELAY_URL` is the OpenAI-compatible base exposed by New API
> (`/v1`). `EMPEROR_RELAY_KEY` is the token from step 4 (sent as
> `Authorization: Bearer <key>`).

After this, `MultiModelRouter` routes **every** model through the relay:
model IDs are passed through verbatim and the relay performs the
OpenAI⇄Claude⇄Gemini format conversion. Emperor-core needs `litellm`
installed (`requirements.txt` already lists it) for live calls to execute.

## How it feeds distillation

Every genuine call made via the relay is recorded by New API (full logs), and
— when a `DistillationStore` is wired into `RealLLMExecutor` — also captured as
a `DistillationTrace` in emperor-core. The offline mock executor never writes
to the trace store, so the corpus stays honest (real answers only).

---

## Environment variables (New API)

| Variable               | Required | Notes                                            |
|------------------------|----------|--------------------------------------------------|
| `SESSION_SECRET`       | yes      | Long random string; required for stable sessions |
| `TZ`                   | no       | e.g. `Asia/Shanghai`                             |
| `MEMORY_CACHE_ENABLED` | no       | In-memory cache (recommended `true`)             |
| `ERROR_LOG_ENABLED`    | no       | Error logging (`true` recommended)               |
| `BATCH_UPDATE_ENABLED` | no       | Batch quota updates (`true` recommended)         |
| `NODE_NAME`            | no       | Human-readable node label in the panel           |
| `SQL_DSN`              | no       | Postgres DSN; **unset ⇒ default SQLite on /data**|
| `REDIS_CONN_STRING`    | no       | Redis; optional cache/session backend            |

## Troubleshooting

- **Port conflict:** change `"3000:3000"` to `"8080:3000"` (and update
  `EMPEROR_RELAY_URL` accordingly).
- **Data not persisting:** ensure the `./data:/data` volume is mounted.
- **401 from emperor-core:** verify `EMPEROR_RELAY_KEY` matches the token
  created in step 4, and that the channel for the requested model is enabled.
