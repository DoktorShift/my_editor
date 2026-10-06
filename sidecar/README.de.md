# Mitgliedschafts-Sidecar für MyEditor

English version: [README.md](README.md)

Ein kleiner Dienst, mit dem man direkt aus MyEditor heraus Mitglied im Verein **EINUNDZWANZIG** werden kann, ohne dass der API-Schlüssel des Vereins jemals in der App landet.

Jede Anwendung, über die sich Leute beim Verein anmelden dürfen, bekommt vom Verein einen eigenen Client-Schlüssel. Einen Schlüssel in einer Desktop-App kann jeder auslesen, deshalb liegt er stattdessen hier. MyEditor schickt seine Mitgliedschaftsanfragen an diesen Dienst. Der Dienst prüft sie, fügt den Schlüssel hinzu und leitet sie an den Verein weiter.

Sonst speichert der Dienst nichts: keine Konten, keine Datenbank, keine Nutzerdaten. Er läuft rund um die Uhr und gehört zum jeweiligen Release-Kanal. Für offizielle Builds ist das der Server von rinbal, `https://e21.rinbal.de`, eingetragen in `MEMBERSHIP_SERVICE_URL` in `constants.py`. Wenn du MyEditor selbst baust und veröffentlichst, betreibst du deinen eigenen.

**Ohne Sidecar oder mit einem Sidecar ohne Schlüssel** bietet MyEditor den Beitritt in der App nicht an. Das Mitgliedschaftsfenster zeigt dann stattdessen "Join on the Website", damit niemand in einen Fehler läuft. Ihr Relay und ihren Medienserver bekommen Mitglieder so oder so: MyEditor erkennt sie an der öffentlichen Mitgliederliste des Vereins.

---

## So funktioniert es

```
MyEditor ──HTTPS──▶ sidecar ──HTTPS + X-Api-Key──▶ verein.einundzwanzig.space
          signiert vom     prüft, drosselt,              prüft erneut,
          Nutzer           ergänzt den Schlüssel         antwortet
```

**Was er weiterleitet.** Nur die Mitgliedschafts-API des Vereins unter `/api/v1/membership`:

| Methode | Pfad |
|---|---|
| GET | `/config` |
| GET, DELETE | `/me` |
| POST | `/applications` |
| POST | `/payments/{year}/invoice` |
| POST | `/payments/{year}/refresh` |
| GET | `/payments` |
| GET | `/export` |

Alles andere bekommt 404 mit `"code": "not_forwarded"`: jeder andere Pfad, jede andere Methode auf diesen Pfaden (auch `PUT /me` bekommt 404, nicht 405) und jede Anfrage mit Query-String. GET- und DELETE-Anfragen haben keinen Body; wird trotzdem einer mitgeschickt, wird die Anfrage abgelehnt (400), ebenso bei einem Content-Type (415). Ein POST-Body muss `application/json` sein.

**Was er prüft, bevor er den Schlüssel einsetzt.** Jeder Aufruf außer `/config` muss eine NIP-98-Signatur der Person tragen, die beitreten will, und diese Signatur muss:

- genau für diese Anfrage die URL des Vereins selbst nennen;
- zur Methode der Anfrage passen;
- zum SHA-256 genau des mitgeschickten Bodys passen;
- jünger als eine Minute sein;
- gültig signiert sein;
- zum ersten Mal verwendet werden.

**Limits.** Anfragen pro Client-Adresse und Minute sowie Rechnungen pro Nostr-Konto und Tag sind begrenzt, weil sich alle das Kontingent des Schlüssels teilen. Ein IPv6-Client zählt nach seinem /64-Netz, denn ein Haushalt oder Server kann jede Adresse darin nutzen. Anfrage-Bodys sind auf 32 KB begrenzt und werden abgelehnt, sobald sie diese Grenze überschreiten; liegt schon die angegebene Länge darüber, werden sie gar nicht erst gelesen. Antworten des Vereins sind auf 1 MB begrenzt, gezählt nach dem Entpacken, falls der Verein sie komprimiert.

**Genau ein Prozess.** Die Limits und die Liste der bereits verwendeten Signaturen liegen im Arbeitsspeicher des Sidecars, also lass genau einen Prozess laufen: kein `--workers` für uvicorn, kein `docker compose up --scale`, keine mehreren Kopien hinter einem Load Balancer. Ein zweiter Prozess würde jedes Limit verdoppeln und könnte dieselbe Signatur ein zweites Mal akzeptieren.

**Antworten.** Sie gehen unverändert durch, einschließlich `Retry-After`. Es gibt zwei Ausnahmen. Sollte der Verein den Schlüssel jemals zurückschicken, entfernt der Sidecar ihn, auch wenn er JSON-escaped oder prozentkodiert darin steht. Und lehnt der Verein eine Anfrage, die alle obigen Prüfungen bestanden hat, mit 401 ab (oder die Abfrage des Mitgliedsbeitrags mit 401 oder 403), dann hat er den Schlüssel abgelehnt oder die Uhren weichen voneinander ab: Der Sidecar schreibt eine Warnung ins Log und antwortet mit 503 und `"code": "upstream_refused"`, und MyEditor meldet, dass der Beitritt in der App gerade nicht möglich ist, und bietet die Website an.

**Was er selbst beantwortet:**

| Endpunkt | Antwort |
|---|---|
| `GET /status` | `{"service": "myeditor-sidecar", "version": "1", "membership": true}`. MyEditor fragt das ab, bevor es den Beitritt anbietet; `false` heißt, dass kein Schlüssel eingerichtet ist. |
| `GET /healthz` | `{"ok": true}`, für Uptime-Monitore. |

**Datenschutz.** Der Schlüssel wird nie geloggt und ist nie Teil einer Antwort. Was der Sidecar ins Log schreibt:

- eine Zeile pro Mitgliedschaftsanfrage: Methode, Pfad, Status, die ersten 8 Zeichen des öffentlichen Schlüssels der signierenden Person (bei signierten Anfragen) und die Dauer;
- bei einer abgelehnten Signatur eine weitere Zeile mit dem Grund, etwa `refused GET /me: time window`;
- Warnungen zum Verein: nicht erreichbar, Antwort zu groß oder unlesbar, Schlüssel abgelehnt;
- die Start- und Stoppmeldungen von uvicorn.

Client-Adressen werden nicht geloggt. Das Access-Log von uvicorn, das sie erfassen würde, ist abgeschaltet (`--no-access-log` im Dockerfile und in der systemd-Unit; behalte das bei, wenn du uvicorn selbst startest), und das eigene Anfrage-Log des HTTP-Clients bleibt stumm. Adressen liegen nur im Arbeitsspeicher, für ein, zwei Minuten, für das Limit pro Adresse. Dein Reverse Proxy führt eigene Logs: Caddy führt in der Konfiguration hier kein Access-Log, nginx dagegen schon, solange du nicht `access_log off;` setzt.

---

## Schlüssel beantragen

Schlüssel vergibt der Verein. Frag im EINUNDZWANZIG-Gruppenraum danach: <https://group.einundzwanzig.space/rooms/42466283723001275>.

Gib dabei an, dass der Schlüssel für einen MyEditor-Mitgliedschafts-Sidecar ist, auf welchem Server er läuft und wer ihn betreibt.

---

## Betrieb mit Docker (empfohlen)

Du brauchst einen Server mit Docker und einen Domainnamen, der auf ihn zeigt (zum Beispiel `e21.example.org`). Caddy ist schon dabei und holt und erneuert das HTTPS-Zertifikat selbstständig. Die Uhr des Servers muss stimmen, weil Signaturen nur eine Minute gültig sind: Halte sie per NTP synchron (auf den meisten Systemen mit `timedatectl set-ntp true`).

1. Hol dir den Code:

   ```sh
   git clone https://github.com/rinbal/my_editor.git
   cd my_editor/sidecar
   ```

2. Leg die Konfigurationsdatei an und trag dort `E21_API_KEY` und `SIDECAR_DOMAIN` ein:

   ```sh
   cp .env.example .env
   chmod 600 .env
   nano .env
   ```

3. Starte ihn:

   ```sh
   docker compose up -d --build
   ```

4. Prüf, ob er läuft:

   ```sh
   curl https://e21.example.org/status
   ```

   In der Antwort sollte `"membership": true` stehen.

Zum Aktualisieren führst du im Ordner `sidecar` erst `git pull` und dann `docker compose up -d --build` aus.

**IPv6-Clients hinter Docker.** Solange IPv6 nicht in Docker selbst aktiviert ist, reicht der Docker-Proxy, der die veröffentlichten Ports 80 und 443 bedient, IPv6-Verbindungen so an Caddy weiter, als kämen sie vom Gateway des Docker-Netzwerks. Caddy und damit auch das Limit pro Adresse sehen dann für alle IPv6-Clients dieselbe Adresse, und alle teilen sich ein Limit. Wenn sich viele deiner Nutzer über IPv6 verbinden, aktiviere IPv6 in Docker (`"ipv6": true` und `"ip6tables": true` in `/etc/docker/daemon.json` sowie `enable_ipv6: true` mit einem IPv6-Subnetz für das Compose-Netzwerk) oder lass Caddy mit `network_mode: host` laufen.

---

## Betrieb ohne Docker

Dieser Weg ist für Server gedacht, auf denen schon ein Reverse Proxy (Caddy oder nginx) für HTTPS läuft. Wie bei Docker gilt: Halte die Uhr des Servers per NTP synchron.

1. Leg einen Benutzer an und hol dir den Code:

   ```sh
   sudo useradd --system --home /opt/myeditor --shell /usr/sbin/nologin myeditor-sidecar
   sudo git clone https://github.com/rinbal/my_editor.git /opt/myeditor
   ```

2. Installiere die Abhängigkeiten:

   ```sh
   sudo python3 -m venv /opt/myeditor/.venv-sidecar
   sudo /opt/myeditor/.venv-sidecar/bin/pip install -r /opt/myeditor/sidecar/requirements.txt
   ```

3. Leg die Konfigurationsdatei an. Nimm die Variablen aus `.env.example`; `SIDECAR_DOMAIN` brauchst du hier nicht.

   ```sh
   sudo cp /opt/myeditor/sidecar/.env.example /etc/myeditor-sidecar.env
   sudo chmod 600 /etc/myeditor-sidecar.env
   sudo nano /etc/myeditor-sidecar.env
   ```

4. Installiere und starte den Dienst:

   ```sh
   sudo cp /opt/myeditor/sidecar/myeditor-sidecar.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now myeditor-sidecar
   ```

Der Dienst lauscht auf `127.0.0.1:8021`. Richte deinen Reverse Proxy darauf aus.

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
    # ssl_certificate und ssl_certificate_key wie bei deinen anderen Seiten

    client_max_body_size 64k;

    location / {
        proxy_pass http://127.0.0.1:8021;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Das Body-Limit weist zu große Anfragen ab, bevor sie den Sidecar erreichen (der selbst alles über 32 KB ablehnt). Mit `X-Forwarded-For` sieht das Limit pro Adresse die echten Adressen; ohne den Header scheint jede Anfrage vom Proxy zu kommen, und alle teilen sich ein Limit.

---

## MyEditor mit dem Sidecar verbinden

| Wer | Wie |
|---|---|
| Offizielle Builds | Setze `MEMBERSHIP_SERVICE_URL` in `constants.py` auf die HTTPS-Adresse des Sidecars, z. B. `https://e21.example.org`. |
| Dein eigener Build | Genauso, mit der Adresse deines eigenen Sidecars. Lass den Wert leer, wenn nur die Website angeboten werden soll. |
| Testen, ohne neu zu bauen | Starte MyEditor mit gesetzter Umgebungsvariable: `MYEDITOR_MEMBERSHIP_SERVICE=https://e21.example.org` |

MyEditor akzeptiert nur Adressen mit `https://`, zur Entwicklung außerdem `http://` auf genau `localhost`, `127.0.0.1` oder `[::1]`, und niemals eine Adresse mit Benutzername, Query oder Fragment.

---

## Konfiguration

| Variable | Standardwert | Bedeutung |
|---|---|---|
| `E21_API_KEY` | leer | Der Client-Schlüssel des Vereins. Leer heißt: Der Beitritt in der App wird nicht angeboten. |
| `SIDECAR_DOMAIN` | | Der öffentliche Name des Servers, für das HTTPS-Zertifikat (nur beim Docker-Setup). |
| `E21_UPSTREAM` | `https://verein.einundzwanzig.space` | Die API des Vereins. Ändere das nur für ein Testsystem, und starte MyEditor dann mit `MYEDITOR_MEMBERSHIP_UPSTREAM` auf derselben Adresse (siehe "Entwickeln und testen"). |
| `SIDECAR_RATE_PER_MINUTE` | `60` | Anfragen pro Client-Adresse und Minute. |
| `SIDECAR_INVOICES_PER_DAY` | `10` | Rechnungen pro Nostr-Konto und Tag. |
| `SIDECAR_LOG_LEVEL` | `INFO` | Bei `WARNING` werden nur Probleme geloggt. |

---

## Entwickeln und testen

Im Wurzelverzeichnis des Repositorys:

```sh
.venv/bin/pip install -r sidecar/requirements.txt
.venv/bin/python -m pytest tests/test_sidecar.py
```

Lokal starten:

```sh
E21_API_KEY=your-test-key .venv/bin/uvicorn sidecar.app:app --port 8021 --no-access-log
```

Dann MyEditor mit diesem Sidecar starten:

```sh
MYEDITOR_MEMBERSHIP_SERVICE=http://localhost:8021 .venv/bin/python main.py
```

**Gegen ein Testsystem des Vereins.** Die Signaturen von MyEditor nennen die Adresse des Vereins, denn genau die prüft der Verein, und der Sidecar lehnt jede Signatur ab, die nicht sein eigenes `E21_UPSTREAM` nennt. Wenn der Sidecar also an ein Testsystem oder einen lokalen Ersatz weiterleitet, gib MyEditor über `MYEDITOR_MEMBERSHIP_UPSTREAM` dieselbe Adresse mit:

```sh
E21_API_KEY=your-test-key E21_UPSTREAM=http://localhost:8000 \
    .venv/bin/uvicorn sidecar.app:app --port 8021 --no-access-log

MYEDITOR_MEMBERSHIP_SERVICE=http://localhost:8021 \
MYEDITOR_MEMBERSHIP_UPSTREAM=http://localhost:8000 \
    .venv/bin/python main.py
```

Beide nehmen Adressen mit `https://` an, oder `http://` auf diesem Rechner (`localhost`, `127.0.0.1`, `[::1]`). Ohne `MYEDITOR_MEMBERSHIP_UPSTREAM` signiert MyEditor für `https://verein.einundzwanzig.space`, wie jeder ausgelieferte Build, und ein Sidecar mit einem anderen `E21_UPSTREAM` lehnt jede signierte Anfrage mit 401 ab.

`tests/smoke_sidecar_e2e.py` erledigt das alles mit einem simulierten Verein: Es schickt den Client der App selbst durch den echten Sidecar.

---

## Fehlersuche

| Symptom | Ursache |
|---|---|
| `/status` meldet `"membership": false` | `E21_API_KEY` ist leer oder wurde nicht geladen. Prüf `.env` (Docker) bzw. `/etc/myeditor-sidecar.env` (systemd) und starte dann neu. |
| MyEditor bietet nur "Join on the Website" an | MyEditor kennt keine Sidecar-Adresse, oder `/status` ist nicht erreichbar oder meldet `false`. Öffne `https://your-sidecar/status` im Browser. |
| Jeder Aufruf endet mit "could not confirm it is you" (401) | Die Uhr des Rechners geht mehr als eine Minute falsch. Signaturen sind nur eine Minute gültig. Schalte die automatische Einstellung von Datum und Uhrzeit ein. |
| MyEditor meldet "Joining in the app isn't available right now", obwohl `/status` `true` meldet, und im Log steht "association refused the key or clocks differ" | Der Verein akzeptiert `E21_API_KEY` nicht mehr (frag nach einem neuen Schlüssel), oder die Uhr dieses Servers geht falsch. Signaturen sind nur eine Minute gültig, halte die Uhr also per NTP synchron (auf den meisten Systemen: `timedatectl set-ntp true`). |
| 502 "not reachable" | Die API des Vereins ist ausgefallen, oder dieser Server erreicht sie nicht. |
