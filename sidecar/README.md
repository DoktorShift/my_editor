# MyEditor membership sidecar

A small service that lets people join the **EINUNDZWANZIG** association from inside MyEditor, without the association's API key ever being in the app.

The association gives each application that may sign people up a client key. A key inside a desktop app can be pulled out by anyone, so the key lives here instead. MyEditor sends its membership requests to this service. The service checks them, adds the key, and forwards them to the association.

The service holds nothing else: no accounts, no database, no user data. It runs all the time next to the release channel. For official builds that is rinbal's server. If you build and publish MyEditor yourself, run your own.

**Without a sidecar, or with a sidecar that has no key,** MyEditor does not offer joining in the app. The membership window offers "Join on the Website" instead, so nobody runs into an error. Members still get their relay and media server either way: MyEditor recognizes them from the association's public member list.

---

## How it works

```
MyEditor ──HTTPS──▶ sidecar ──HTTPS + X-Api-Key──▶ verein.einundzwanzig.space
          signed by        checks, rate-limits,          checks again,
          the user         adds the key                  answers
```

**What it forwards.** Only the association's membership API under `/api/v1/membership`:

| Method | Path |
|---|---|
| GET | `/config` |
| GET, DELETE | `/me` |
| POST | `/applications` |
| POST | `/payments/{year}/invoice` |
| POST | `/payments/{year}/refresh` |
| GET | `/payments` |
| GET | `/export` |

Everything else gets 404.

**What it checks before lending the key.** Every call except `/config` must carry a NIP-98 signature from the person joining, and the signature must:

- name the association's own URL for exactly this request;
- match the request's method;
- match the SHA-256 of exactly the body sent;
- be less than a minute old;
- be validly signed;
- be used for the first time.

**Limits.** Requests per client address per minute and invoices per Nostr account per day are limited, because everyone shares the key's quota. Request bodies are capped at 32 KB and refused as soon as they pass that, unread when their declared length already does. Answers from the association are capped at 1 MB, counted after unpacking should the association compress them.

**Answers.** They pass through unchanged, `Retry-After` included. Two exceptions. If the association ever echoed the key back, the sidecar removes it. And if the association refuses with 401 a request that passed every check above (or the fee lookup with 401 or 403), it refused the key or the clocks differ: the sidecar logs a warning and answers 503 with `"code": "upstream_refused"`, and MyEditor says joining in the app isn't available right now and offers the website.

**What it answers itself:**

| Endpoint | Answer |
|---|---|
| `GET /status` | `{"service": "myeditor-sidecar", "version": "1", "membership": true}`. MyEditor asks this before offering to join; `false` means no key is configured. |
| `GET /healthz` | `{"ok": true}`, for uptime monitors. |

**Privacy.** The key is never logged and never part of an answer. The log has one line per request with the method, path, status, the first 8 characters of the user's public key, and the time taken. Nothing else.

---

## Get a key

The association issues keys. Ask in the EINUNDZWANZIG group room: <https://group.einundzwanzig.space/rooms/42466283723001275>.

Say that the key is for a MyEditor membership sidecar, the server it runs on, and who operates it.

---

## Run it with Docker (recommended)

You need a server with Docker, and a domain name pointing at it (for example `e21.example.org`). Caddy, included here, gets and renews the HTTPS certificate on its own.

1. Get the code:

   ```sh
   git clone https://github.com/rinbal/my_editor.git
   cd my_editor/sidecar
   ```

2. Create the configuration file and set `E21_API_KEY` and `SIDECAR_DOMAIN` in it:

   ```sh
   cp .env.example .env
   chmod 600 .env
   nano .env
   ```

3. Start it:

   ```sh
   docker compose up -d --build
   ```

4. Check it:

   ```sh
   curl https://e21.example.org/status
   ```

   The answer should say `"membership": true`.

To update, run `git pull`, then `docker compose up -d --build` in the `sidecar` folder.

---

## Run it without Docker

Use this on a server that already runs a reverse proxy (Caddy or nginx) for HTTPS.

1. Create a user and get the code:

   ```sh
   sudo useradd --system --home /opt/myeditor --shell /usr/sbin/nologin myeditor-sidecar
   sudo git clone https://github.com/rinbal/my_editor.git /opt/myeditor
   ```

2. Install the dependencies:

   ```sh
   sudo python3 -m venv /opt/myeditor/.venv-sidecar
   sudo /opt/myeditor/.venv-sidecar/bin/pip install -r /opt/myeditor/sidecar/requirements.txt
   ```

3. Create the configuration file. Use the variables from `.env.example`; `SIDECAR_DOMAIN` is not needed here.

   ```sh
   sudo cp /opt/myeditor/sidecar/.env.example /etc/myeditor-sidecar.env
   sudo chmod 600 /etc/myeditor-sidecar.env
   sudo nano /etc/myeditor-sidecar.env
   ```

4. Install and start the service:

   ```sh
   sudo cp /opt/myeditor/sidecar/myeditor-sidecar.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now myeditor-sidecar
   ```

The service listens on `127.0.0.1:8021`. Point your reverse proxy at it.

Caddy:

```
e21.example.org {
	request_body {
		max_size 64KB
	}
	reverse_proxy 127.0.0.1:8021
}
```

nginx:

```nginx
server {
    listen 443 ssl;
    server_name e21.example.org;
    # ssl_certificate and ssl_certificate_key as for your other sites

    client_max_body_size 64k;

    location / {
        proxy_pass http://127.0.0.1:8021;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

The body limit turns away oversized requests before they reach the sidecar (which refuses anything over 32 KB itself). `X-Forwarded-For` lets the per-address limit see real addresses; without it, every request seems to come from the proxy and shares one limit.

---

## Point MyEditor at it

| Who | How |
|---|---|
| Official builds | Set `MEMBERSHIP_SERVICE_URL` in `constants.py` to the sidecar's HTTPS address, e.g. `https://e21.example.org`. |
| Your own build | Same, with your own sidecar's address. Leave it empty to offer the website only. |
| Testing, without rebuilding | Start MyEditor with the environment variable set: `MYEDITOR_MEMBERSHIP_SERVICE=https://e21.example.org` |

MyEditor accepts only `https://` addresses, plus `http://localhost` for development.

---

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `E21_API_KEY` | empty | The association's client key. Empty means joining in the app is not offered. |
| `SIDECAR_DOMAIN` | | The server's public name, for the HTTPS certificate (Docker setup only). |
| `E21_UPSTREAM` | `https://verein.einundzwanzig.space` | The association's API. Change it only for a test system. |
| `SIDECAR_RATE_PER_MINUTE` | `60` | Requests per client address per minute. |
| `SIDECAR_INVOICES_PER_DAY` | `10` | Invoices per Nostr account per day. |
| `SIDECAR_LOG_LEVEL` | `INFO` | `WARNING` logs only problems. |

---

## Develop and test

From the repository root:

```sh
.venv/bin/pip install -r sidecar/requirements.txt
.venv/bin/python -m pytest tests/test_sidecar.py
```

Run it locally:

```sh
E21_API_KEY=your-test-key .venv/bin/uvicorn sidecar.app:app --port 8021
```

Then start MyEditor against it:

```sh
MYEDITOR_MEMBERSHIP_SERVICE=http://localhost:8021 .venv/bin/python main.py
```

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `/status` says `"membership": false` | `E21_API_KEY` is empty or not loaded. Check `.env` (Docker) or `/etc/myeditor-sidecar.env` (systemd), then restart. |
| MyEditor offers only "Join on the Website" | MyEditor has no sidecar address, or `/status` cannot be reached or says `false`. Open `https://your-sidecar/status` in a browser. |
| Every call ends with "could not confirm it is you" (401) | The computer's clock is more than a minute off. Signatures are only valid for a minute. Turn on automatic date and time. |
| MyEditor says "Joining in the app isn't available right now" although `/status` says `true`, and the log says "association refused the key or clocks differ" | The association no longer accepts `E21_API_KEY` (ask for a new one), or this server's clock is off. Signatures are valid for a minute only, so keep the clock synchronized with NTP (on most systems: `timedatectl set-ntp true`). |
| 502 "not reachable" | The association's API is down, or this server cannot reach it. |
