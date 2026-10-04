# Lab Seminar Planner

A small web application for planning a semester of student seminars: the lab publishes a pool of topics, students say what they would like to attend and whether they would present, organisers turn the votes into a programme of about eight sessions, and students then record whether they expect to attend each confirmed session.

It implements [docs/SRS.md](docs/SRS.md). The interface is available in **English and Chinese (中文)**, including the topic content. The stack is deliberately plain so students can maintain it: **Python, FastAPI, SQLAlchemy, SQLite and server-rendered Jinja2 pages** (no JavaScript build step).

## Quick start

Requires Python 3.11 or newer (developed and tested with 3.12).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create the first organiser account (you are prompted for the password, which is stored hashed):

```bash
python -m app.cli create-user alice --display-name "Alice Example" --role organiser
```

Start the server:

```bash
export LSP_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000> and log in. The database (`data/seminar.db`) and its tables are created automatically on first start. Everything else, including adding students, is done in the web interface under **Organiser → Users**.

> Keep the same `LSP_SECRET_KEY` between restarts (put it in your service definition or a private `.env` you load yourself). If it is not set, a random key is generated each start and everybody is logged out whenever the server restarts.

### Trying it out with sample data

To explore the app with ready-made users, topics and votes, seed a **separate, empty** database:

```bash
LSP_DATABASE_URL=sqlite:///demo.db python -m app.cli seed-demo      # prints random passwords once
LSP_DATABASE_URL=sqlite:///demo.db uvicorn app.main:create_app --factory
```

`seed-demo` creates one organiser and eight students with random votes, and imports the topics from `docs/sample_topics.json` (see [Topic data](#topic-data-and-importing-it)). That file is sample data kept **out of version control**, so on a fresh clone either put it there or pass `--topics-file`. `seed-demo` refuses to run if the database already contains users. Do not use demo accounts for real data.

## Language

Every page has an **English / 中文** switch in the header. The choice is remembered in a cookie (it survives logging out); until someone chooses, the browser's language preference decides, with English as the fallback. The JSON API always answers in English.

- **Interface text** is written in English in the code and translated through the catalogue in [app/i18n/zh.py](app/i18n/zh.py). A string without a Chinese entry simply shows in English, and a test fails if any marked string lacks one, so when you add or reword interface text, add the Chinese line too.
- **Topic content** is stored in two versions: the topic itself holds the English text, and a *translation* row holds the Chinese title, description and keywords. Organisers edit both on the topic form, and users see the version for their language, falling back to English when a topic has no Chinese version. **Organiser → Topics** shows which topics still lack one.
- **Categories** and the **semester name** are bilingual too, under **Organiser → Settings & voting**. Categories are entered one per line as `identifier | English name | Chinese name`.
- Translations are prepared in advance and stored; nothing is machine-translated while the app runs.

## Topic data and importing it

`python -m app.cli import-topics [FILE]` loads topics into the database (the default file is `docs/sample_topics.json`). The file holds lists of topics in the same shape as the API's topic input:

```json
{
  "core_topics":     [{"title": "...", "description": "...", "category": "software-engineering",
                       "topic_type": "core", "keywords": ["Git", "debugging"], "status": "active"}],
  "elective_topics": [{"title": "...", "description": "...", "category": "robotics",
                       "topic_type": "optional", "keywords": ["VLA"], "status": "active"}]
}
```

The Chinese version is a second file next to it, with the same name plus `.zh.json` (`docs/sample_topics.zh.json`), keyed by each topic's **English title**:

```json
{
  "language": "zh",
  "topics": {"<English title>": {"title": "...", "description": "...", "keywords": ["..."]}},
  "category_labels": {"software-engineering": "软件工程"}
}
```

- Categories used by the topics are added to the configured list automatically (with the names from `category_labels`).
- Importing is safe to repeat. Topics whose English title already exists are skipped, and only a *missing* translation is filled in, so edits made in the web interface are never overwritten.
- Topics without an entry in the translations file are imported in English only and listed in the output, as are entries that match no topic.
- Topics are owned by the first organiser (`--created-by USERNAME` picks another), so create one first.
- `docs/sample_topics.json` and `docs/sample_topics.zh.json` are the project's *sample* data and are listed in `.gitignore`. Replace them with your own data (same format) whenever you like.

## Configuration

All settings are environment variables.

| Variable | Default | Meaning |
|---|---|---|
| `LSP_SECRET_KEY` | random per start | Signs session cookies. **Set this** for any real use. |
| `LSP_DATABASE_URL` | `sqlite:///<project>/data/seminar.db` | SQLite database. The default is anchored to the project folder, so it does not depend on where you start the server. |
| `LSP_COOKIE_SECURE` | `false` | Set to `true` when served over HTTPS so the session cookie is only sent over HTTPS. |
| `LSP_SESSION_MAX_AGE` | `604800` (7 days) | Login lifetime in seconds. |

### Deployment notes

- **HTTPS.** If the app is reachable from the public Internet it must be served over HTTPS. Run uvicorn on `127.0.0.1` behind a reverse proxy that terminates TLS (for example Caddy: `seminars.example.org { reverse_proxy 127.0.0.1:8000 }`) and set `LSP_COOKIE_SECURE=true`.
- **Backups.** Everything lives in one SQLite file. Take a consistent copy while the app is running with `sqlite3 data/seminar.db ".backup 'backup.db'"`.
- **Workers.** One uvicorn process is plenty for a lab group. Sessions are signed cookies, so more workers also work as long as they share `LSP_SECRET_KEY`.

## Importing accounts in bulk

**Organiser → Users → Import accounts from a file** creates many accounts from a CSV file. Download the template there (or use `accounts-template.csv` from `/organiser/users/template.csv`), fill it in with a spreadsheet, save it as CSV and upload it.

| Column | Required | Meaning |
|---|---|---|
| `username` | yes | Login name, stored in lower case, 1-64 characters without spaces. |
| `display_name` | yes | Name shown in the interface. |
| `role` | no | `student` (the default) or `organiser`. |
| `year_group` | no | For example `UG2`. |
| `password` | no | Leave it empty and a password is generated. |

```csv
username,display_name,role,year_group,password
alice,Alice Wang,student,UG2,
bob,Bob Li,organiser,PG1,
```

- **All or nothing.** If anything in the file is wrong (a bad username, a password under 8 characters, a username that already exists or is repeated, an unknown role, a row with more values than columns...) no account is created and every problem is listed with its row number. Row 1 is the header, so row numbers match your spreadsheet.
- **Passwords.** Generated passwords (10 characters, without look-alike letters) are shown **once** on the result page, which the browser is told not to cache, and can be downloaded as a CSV. Passwords you typed into the file are never shown again. Ask people to change theirs under **Account**, and delete the roster file if it contains passwords.
- **Encodings and separators.** UTF-8 (with or without a byte-order mark) and GBK/GB18030, which Excel on Chinese Windows uses for "CSV", are both accepted, as are files separated by semicolons or tabs. A value containing a comma must be in quotes.
- **Limits.** At most 200 accounts and 256 KB per file; import larger rosters in parts.

## A typical semester

1. **Users** – the organiser creates an account for each student (**Organiser → Users**), one at a time or by [importing a CSV roster](#importing-accounts-in-bulk), and hands over the initial passwords privately. Everyone can change their own password under **Account**.
2. **Settings** – set the semester name, the target numbers (default 8 sessions: 4 core + 4 optional) and, if needed, the topic categories.
3. **Topics** – create the topic pool (**Organiser → Topics**), or import it with `import-topics`. Mark each topic *core* (always runs) or *optional* (competes for a slot), and choose its status: *draft* topics stay hidden from students, *active* ones appear in the pool. (The form defaults to *active*; topics created through the API default to *draft*.) Add the Chinese title and description in the second section of the form.
4. **Proposals** – students suggest topics (**Propose a topic**). Organisers edit, approve, reject or archive them (**Organiser → Proposals**). Approving creates an active topic.
5. **Voting** – open voting in **Settings & voting**. Students rate their interest in each optional topic and say whether they would lead, co-present or maybe present any topic. **Results** shows totals only.
6. **Planning** – **Organiser → Planning & sessions** shows a suggested plan. Tick the topics you want (override the suggestion freely), give each a teaching week, and create provisional sessions.
7. **Sessions** – open a session to set its date, assign presenters (volunteers are listed first), replace its topic, or cancel it.
8. **Publish** – confirm the provisional sessions. The **Semester plan** page shows provisional and confirmed sessions side by side; students can now record their expected attendance for confirmed ones.

## How the main rules work

**Interest score** = 3 × (very interested) + 2 × (interested) + 1 × (slightly interested). "No preference" adds nothing. Interest is collected for optional topics only; core topics collect presentation preference only. Preferences of deactivated users are not counted.

**Candidate plan** (deterministic; it never creates or confirms sessions by itself):

1. All active core topics are included, whatever their score.
2. Active optional topics are ranked by: having at least one `lead` or `co_present` volunteer (a lone `maybe` does not count), then interest score, then number of `lead` volunteers, then topic id.
3. The top *N* (the optional target) are recommended and the next two are listed as alternatives.
4. Topics in the plan with no volunteers (`NO_PRESENTER`) or no lead (`NO_LEAD_PRESENTER`) get warnings, as do mismatches against the configured targets.

**Topic statuses:** `draft` (organiser-only), `active` (in the pool, open for preferences), `closed` (visible and schedulable, but preferences are frozen), `archived` (hidden, cannot be scheduled).

**Session statuses:** `proposed` (shown as *Provisional*) → `confirmed` → `completed`, or `cancelled`. A session needs a teaching week or a date to be confirmed. Attendance can only be recorded while a session is `confirmed`. Allowed moves: proposed→confirmed/cancelled; confirmed→proposed/completed/cancelled; completed→confirmed (to correct a mistake); cancelled→proposed (to reinstate).

**Voting** starts *closed* on a fresh install; the organiser opens it when the pool is ready. While closed, nobody can change preferences. Resetting a preference deletes its record; saving 0 / none through the API stores that value. Both mean "no preference" and score the same.

**Privacy:** students only see aggregate counts. Organisers can see who volunteered to present (needed to assign presenters) and individual attendance intentions, but not other students' interest ratings.

## API

The JSON API lives under `/api` and follows SRS section 8; interactive documentation is at `/api/docs` while the server runs (it loads its page assets from a CDN, so it needs Internet access). Authentication is the same session cookie the web pages use: `POST /api/auth/login` first. Errors use `{"error": {"code": "...", "message": "..."}}`.

Differences from the SRS suggestions:

- `DELETE /api/topics/{id}` and `DELETE /api/sessions/{id}` are **not provided**; archive a topic or cancel a session instead, as the SRS recommends.
- Added: `POST /api/topic-proposals/{id}/archive`, `POST /api/auth/password` (change your own password; body `current_password` and `new_password`; other sessions of that user are signed out), and the optional `ranking` field in the candidate-plan response (every eligible optional topic, best first).
- Topics carry a `translations` object (for example `{"zh": {"title", "description", "keywords"}}`) in responses, and accept it when created or updated: a language's entry replaces the stored one, `null` removes it, and languages you leave out are untouched. Settings gain `semester_name_zh` and `category_labels`.
- Added error codes: `AUTH_REQUIRED`, `VALIDATION_ERROR`, `INVALID_CATEGORY`, `INVALID_PROPOSAL_STATE`, `DUPLICATE_PRESENTER`, `PRESENTER_NOT_FOUND`, `USER_NOT_FOUND`, `DUPLICATE_USER`, `LAST_ORGANISER`, `WEAK_PASSWORD`, `PASSWORD_UNCHANGED`, `CSRF_FAILED`, plus generic `NOT_FOUND`, `CONFLICT` and `INTERNAL_ERROR`.
- `TopicProposal` has an extra nullable `topic_id` linking an approved proposal to the topic it created.
- User management (creating accounts, organiser password resets, activating and deactivating) and the language switch are available through the web pages only.

## Project layout

```text
app/
  main.py          application factory, error handling
  config.py        environment configuration
  database.py      SQLite engine and session factory
  models.py        SQLAlchemy models (SRS section 7)
  schemas.py       Pydantic request/response models
  deps.py          authentication and permission dependencies
  security.py      argon2 password hashing
  i18n/            translation helpers and the Chinese catalogue (zh.py)
  services/        ALL business rules (preferences, results, planning, sessions, ...)
  api/             thin JSON routers calling the services
  web/             thin HTML routers, Jinja2 templates and CSS calling the same services
  cli.py           init-db, create-user, import-topics, seed-demo
tests/             pytest suite
```

The rule of thumb: **business rules live in `services/`**; `api/` and `web/` only translate requests and responses. The database also enforces the important invariants itself (one preference per user and topic, one presenter or attendance record per user and session, valid enum values, interest between 0 and 3).

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite covers login and password change, bulk account import, preference creation/update and duplicate prevention, score calculation, candidate ranking, every organiser-only operation, the proposal workflow, sessions and attendance rules, the web forms (including CSRF on every POST route), the English/Chinese interface (a completeness check on the catalogue, language selection, translated pages), bilingual topic content, and the command-line tools including the topic import.

## Known limitations

- **No schema migrations.** Tables are created on first start, but later changes to the data model would need a manual migration (or adopting Alembic).
- **No login rate limiting or lockout**, and no email-based password reset. Users change their own password under **Account**, and organisers can set a new one for anyone; a changed or reset password signs the user out everywhere else.
- **Logout is client-side.** Sessions are signed cookies, so a copied cookie stays valid until it expires. Deactivating the account or changing its password does cut access immediately.
- **CSRF protection differs by interface.** Web forms carry a per-session token that every POST must present. The JSON API relies on the `SameSite=Lax` session cookie and JSON-only request bodies instead; keep it behind the same origin as the web pages and do not enable cross-origin (CORS) access without adding token checks.
- **Small groups reveal individuals.** Aggregate counts (for example "1 lead presenter") can identify a person when only a few students vote.
- **Last write wins** if the same person edits their preferences in two tabs.
- Account import reads **CSV only** (not `.xlsx`), and there is no preview step: fix the reported rows and upload again.
- Changing a presenter's role means removing and re-adding them. Replacing a provisional session's topic clears its presenters.
- Timetable clashes (two sessions in the same week) are not checked, topics and sessions cannot be hard-deleted, and there are no notifications, calendar export or single sign-on (all out of scope for the first release).
- The interface uses plain forms and full page loads; HTMX (suggested in the SRS) is not used.
- Form errors are shown as a message after reloading the page; typed text in a rejected form is not kept.
- **Only topics, categories and the semester name are bilingual.** Student proposals, session notes and people's names stay in the language they were typed in; an approved proposal becomes a topic with that text, so add the other language by editing the topic. Only English and Chinese are supported.
