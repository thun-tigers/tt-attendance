# tt-attendance

Anwesenheits-Service der Thun-Tigers-Plattform. Der Microservice erlaubt
Trainingsteilnehmenden, sich pro Trainings-Occurrence an- oder abzumelden, und
gibt Coaches ein Dashboard zur Anwesenheitskontrolle sowie eine
Statistikübersicht.

Aktuelle Version: `0.1.16` (siehe [`VERSION`](VERSION)).

## Features

- Selbst-Anmeldung der Spielerinnen und Spieler je Training mit drei möglichen
  Antworten (`attending`, `maybe`, `declined`) und Pflicht-Begründung für
  `maybe` und `declined`.
- Coach-Dashboard mit Detailansicht pro Training, gruppiert nach
  Positionsgruppen (aus `tt-members`).
- Presence-Tracking: Coaches markieren pro Person `present` oder `unexcused`
  (mit Zeitstempel `presence_marked_at`); ändert eine Person ihre Antwort,
  wird das vorherige Coach-Marking automatisch zurückgesetzt.
- Statistikseite je Trainings-Serie (Antwortsummen und Präsenzsummen).
- Deferred-Loading weiterer Trainings über eine JSON-Route.
- Anzeige der offenen Nachrichten-Anzahl in der Navigation, geliefert von
  `tt-members` über internen Endpoint.
- SSO-Login gegen `tt-auth` mit Schutz gegen Token-Replay.
- Service-to-Service-API für andere Plattform-Dienste.

## Architektur

`tt-attendance` läuft als eigener Container in der Thun-Tigers-Plattform und
kommuniziert mit den folgenden Diensten:

- `tt-auth` – SSO-Provider; ausserdem Auflösung von Benutzerprofilen über
  `TT_AUTH_INTERNAL_URL`.
- `tt-agenda` – Quelle der Trainings-Occurrences (Listen und Einzelabruf).
- `tt-members` – Positionsgruppen (`fetch_position_groups`) und Anzahl
  offener Nachrichten pro Benutzer (`/api/internal/messages/count`).
- `tt-infra` – Plattform-Kontext (zentrale Konfiguration, Reverse-Proxy).
- `tt-common` – gemeinsame Python-Bibliothek für UI-Layout (`register_shared_ui`),
  SSO-Helpers (`tt_common.sso`) und Autorisierung (`tt_common.authz`).
- PostgreSQL – persistenter Speicher (Tabellen `users`, `attendances`).
- Redis – optionaler Backend für Rate-Limits (`RATELIMIT_STORAGE_URI`) und
  SSO-Replay-Storage (`SSO_REPLAY_STORAGE_URI`).

Die Kommunikation zwischen Diensten läuft ausschliesslich intern über
`http://<service>:5000` und wird per gemeinsamem Secret abgesichert.

## Tech-Stack

- Python 3.12 (Docker-Basis: `python:3.12-slim`)
- Flask 3.0, Flask-SQLAlchemy 3.1, Flask-Migrate 4, Flask-Limiter 3.9
- WTForms 3.2, email-validator 2.2
- PyJWT 2.8 für SSO-Token
- psycopg 3 (binary) für PostgreSQL
- redis 5
- requests 2.32
- gunicorn 21 als Produktions-WSGI-Server
- `tt-common` v0.1.15 (via Git-Referenz in `requirements.txt`)

## Lokale Entwicklung

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# .env anpassen (Secrets, DB-URI, interne URLs)

python run.py
```

Der Entwicklungsserver bindet standardmässig an Port `5090` (`PORT` in
`run.py`). Beim Start legt die Anwendung das Schema an, wenn
`AUTO_CREATE_DB=true` (Default). Flask-Migrate ist installiert, es liegt
aktuell aber kein `migrations/`-Verzeichnis im Repository; für eine
produktive Migration entweder `flask db init/migrate/upgrade` initialisieren
oder auf `AUTO_CREATE_DB` und den Column-Ensure-Hook in
`app/__init__.py::_ensure_attendance_columns` vertrauen.

## Konfiguration

Alle Werte werden über Umgebungsvariablen gesetzt. Ausgewertet in
[`app/config.py`](app/config.py):

| Variable | Zweck | Default |
| --- | --- | --- |
| `SECRET_KEY` | Flask-Session- und Fallback-Secret | – (Pflicht in Produktion) |
| `SQLALCHEMY_DATABASE_URI` / `DATABASE_URL` | Postgres-Verbindung | `postgresql+psycopg://tt_attendance:...@tt-postgres-attendance:5432/tt_attendance` |
| `AUTO_CREATE_DB` | `db.create_all()` beim Start | `true` |
| `LOG_LEVEL` | Log-Level (`INFO`, `DEBUG`, …) | `INFO` |
| `TZ` | Zeitzone im Container | `Europe/Zurich` |
| `SESSION_COOKIE_NAME` | Cookie-Name | `attendance_session` |
| `SESSION_COOKIE_SECURE` / `_HTTPONLY` / `_SAMESITE` | Cookie-Flags | `true` / `true` / `Lax` |
| `AUTH_BASE_URL` | Öffentliche Basis-URL von `tt-auth` (Redirects) | `http://localhost:8085` |
| `TT_AUTH_INTERNAL_URL` | Interner Endpoint von `tt-auth` | – |
| `TT_AGENDA_INTERNAL_URL` | Interner Endpoint von `tt-agenda` | – |
| `TT_MEMBERS_INTERNAL_URL` | Interner Endpoint von `tt-members` | `http://tt-members:5000` |
| `TT_INFRA_INTERNAL_URL` | Interner Endpoint von `tt-infra` | – |
| `SSO_SHARED_SECRET` | HMAC-Secret für SSO-Token | Fallback: `SECRET_KEY` |
| `SSO_EXPECTED_AUDIENCE` | Erwarteter `aud`-Claim | `tt-attendance` |
| `SSO_TOKEN_EXPIRY_SECONDS` | Ausgabe-Gültigkeit für ausgehende SSO-Token | `60` |
| `SSO_REPLAY_STORAGE_URI` | Redis-URL für Replay-Schutz (leer = In-Memory) | `''` |
| `SSO_REPLAY_TTL_SECONDS` | Aufbewahrung verbrauchter Token-IDs | `300` |
| `INTERNAL_API_SECRET` | Shared Secret für Service-to-Service-Requests | `tt-internal-dev-secret-change-me` |
| `RATELIMIT_STORAGE_URI` | Storage für Flask-Limiter | `memory://` |
| `PORT` | Port für `python run.py` (nicht Gunicorn) | `5090` |

Zusätzlich dokumentiert `.env.example` die Postgres-Bootstrap-Variablen
`POSTGRES_ATTENDANCE_DB`, `POSTGRES_ATTENDANCE_USER` und
`POSTGRES_ATTENDANCE_PASSWORD` für die zentrale Compose-Konfiguration.

## Docker / Deployment

Das Image basiert auf `python:3.12-slim`, installiert die Requirements und
startet `gunicorn` mit 4 Workern gegen `run:app` auf Port `5000`. Es läuft
als nicht-privilegierter User `appuser`. Zeitzone im Container ist
`Europe/Zurich`.

```bash
docker build -t tt-attendance .
docker run --rm -p 5000:5000 --env-file .env tt-attendance
```

Der reguläre Betriebsweg ist der zentral orchestrierte Stack im
Nachbar-Repository `tt-infra`; dort werden Reverse-Proxy, Postgres-Instanz
und Umgebung bereitgestellt.

## Datenmodell

Definiert in [`app/models.py`](app/models.py):

- **`users`** – lokaler Cache der über SSO angemeldeten Konten.
  - `auth_user_id` (unique) referenziert den Benutzer in `tt-auth`.
  - `username`, `display_name`, `platform_role`, `service_role`,
    `claims_json` (vollständige SSO-Claims).
  - `created_at`, `updated_at` (UTC).
- **`attendances`** – eine Zeile pro (`training_id`, `user_id`),
  Unique-Constraint `uq_training_user`.
  - `status` (`attending` | `maybe` | `declined`) – Antwort der Spielerin
    bzw. des Spielers.
  - `presence_status` (`present` | `unexcused` | `NULL`) – vom Coach
    markiert.
  - `presence_marked_at` – Zeitstempel der Coach-Markierung.
  - `reason` – optionale bzw. bei `maybe`/`declined` erforderliche
    Begründung.
  - `created_at`, `updated_at`.

## Endpunkte

Blueprints werden in [`app/__init__.py`](app/__init__.py) registriert.

### UI (`app.routes.attendance`)

| Methode | Pfad | Zweck |
| --- | --- | --- |
| GET | `/` | Startseite: kommende Trainings mit 3-Button-Status |
| GET | `/api/trainings/deferred` | Weitere Trainings als HTML-Fragment nachladen |
| GET | `/coach` | Sprung zum nächsten relevanten Training (Coach) |
| GET | `/coach/statistics` | Übersicht Anwesenheits- und Präsenzsummen |
| GET | `/coach/training/<occurrence_id>` | Detailansicht eines Trainings |
| POST | `/api/trainings/<occurrence_id>/presence` | Coach setzt Präsenz einer Person |
| POST | `/api/trainings/<occurrence_id>/set-status` | Eigener Status (3-Button, AJAX) |

### JSON-API (`app.routes.api`, `url_prefix='/api'`)

| Methode | Pfad | Zweck |
| --- | --- | --- |
| GET/POST | `/api/trainings/<occurrence_id>/attendance` | Status abfragen / setzen |
| GET | `/api/me/attendances` | Eigene Anwesenheiten (mit Trainingsinfos) |
| GET | `/api/coach/trainings/<occurrence_id>` | Coach-Detail als JSON |
| GET | `/api/coach/summary` | Coach-Summenübersicht über alle Trainings |

### Interne Service-to-Service-API

Geschützt über Header `X-TT-Internal-Secret: <INTERNAL_API_SECRET>`.

| Methode | Pfad | Zweck |
| --- | --- | --- |
| GET | `/api/internal/training/<occurrence_id>/counts` | Aggregierte Zählungen für `tt-agenda` |
| GET | `/api/internal/users/<int:user_id>/attendances` | Alle Anwesenheiten eines Users |

### Auth und Betrieb (`app.routes.auth` + `app/__init__.py`)

| Methode | Pfad | Zweck |
| --- | --- | --- |
| GET | `/login` | Redirect zur SSO-Login-URL von `tt-auth` |
| GET/POST | `/logout` | Session leeren und zur SSO-Logout-URL |
| GET | `/auth/sso` | SSO-Callback: Token prüfen, User syncen, Session setzen |
| GET | `/health` | Liveness: `{ "status": "ok", "service": "tt-attendance" }` |

## Auth und SSO

- Login läuft über `tt-auth`. `tt-attendance` gibt selbst nie Zugangsdaten
  entgegen.
- Der SSO-Callback (`/auth/sso`) prüft das JWT mit `SSO_SHARED_SECRET`,
  validiert `SSO_EXPECTED_AUDIENCE` und lehnt bereits verwendete Token
  über `app/sso_replay.py` ab.
- Ausgehende Requests an `tt-auth`/`tt-agenda` verwenden kurzlebige
  SSO-Token (`SSO_TOKEN_EXPIRY_SECONDS`) plus `X-TT-Internal-Secret`.
- Rollenmodell aus den Claims (via `tt_common.authz`): `platform_role`
  (plattformweit) und `service_role` (dienstspezifisch). Zusätzlich werden
  `role_permissions` aus den Claims ausgewertet – z.B. schaltet
  `attendance: [create|write|update|delete|approve]` oder eine der Rollen
  `admin` / `coach` / `head_coach` die Coach-Ansichten frei.

## Tests

```bash
pip install -r requirements.txt
pytest
```

Die Test-Fixtures in [`tests/conftest.py`](tests/conftest.py) bauen die App
mit einer SQLite-Datenbank pro Test (`tmp_path`) und einer dedizierten
`TestConfig`. Die vorhandenen Suites decken Auth-/Agenda-Integration,
Shared-UI-Registrierung und das Seed-Skript ab.

## Seed-Daten

Für die lokale Entwicklung erzeugt
[`scripts/seed_attendance_test_data.py`](scripts/seed_attendance_test_data.py)
deterministische Anwesenheiten für die anstehenden Trainings. Das Skript
zieht aktive Mitglieder aus `tt-auth`, holt Trainings aus `tt-agenda` und
schreibt Antworten gemäss einer Verteilung von 70% `attending`, 10%
`maybe`, 20% `declined`. Wichtige Flags:

```bash
python scripts/seed_attendance_test_data.py --help
# --training-id, --team-code, --limit, --user-id,
# --clear-existing, --seed <int>, --dry-run
```

Das Skript erkennt automatisch, ob es innerhalb eines Containers läuft, und
verwendet die passenden internen bzw. lokalen URLs für die
Nachbar-Services.

## CI/CD

Im Verzeichnis `.github/workflows/` liegen drei Workflows:

- **`00-docker-build.yml` – Container Image.** Baut und publiziert
  Multi-Arch-Images (`linux/amd64`, `linux/arm64`) auf GHCR. Tags:
  `sha-<short>`, `beta` bei `main`, `v<VERSION>` und `latest` bei
  Git-Tag `v*`. Prüft, dass `VERSION` `MAJOR.MINOR.PATCH` entspricht und
  bei Tag-Push exakt zum Git-Tag passt.
- **`01-version-release.yml` – Version and Release.** Validiert die
  `VERSION`-Datei bei jedem Push/PR und erzeugt bei einem `v*`-Tag ein
  GitHub-Release mit generierten Release-Notes und einem Verweis auf das
  passende Container-Image.
- **`02-manual-build.yml` – Manual Image Build.** Manueller Trigger, um
  ein Image unter einem beliebigen Tag (z.B. `dev`, `hotfix`) auf GHCR zu
  veröffentlichen.

## Versionierung und Release

1. Änderung committen und `VERSION` gemäss SemVer bumpen.
2. `main` mergen. `01-version-release.yml` validiert die neue Version.
3. Release erzeugen: Git-Tag `v<VERSION>` (z.B. `v0.1.16`) pushen.
4. `00-docker-build.yml` publiziert `ghcr.io/<owner>/tt-attendance:v0.1.16`
   sowie `:latest`, `01-version-release.yml` erstellt automatisch ein
   GitHub-Release.
5. Für Ad-hoc-Builds ausserhalb dieses Flusses `02-manual-build.yml`
   manuell auslösen.

## Weitere Referenzen

- Plattform-Kontext, zentrale Konfiguration und Proxy-Setup:
  [`../tt-infra/docs/HANDOFF_CENTRAL_CONFIG_AND_PROXY.md`](../tt-infra/docs/HANDOFF_CENTRAL_CONFIG_AND_PROXY.md)
