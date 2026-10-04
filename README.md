# Lab Seminar Planner

A small web application for planning a semester of student seminars: the lab publishes a pool of topics, students say what they would like to attend and whether they would present, organisers turn the votes into a programme of about eight sessions, and students then record whether they expect to attend each confirmed session.

It implements [docs/SRS.md](docs/SRS.md). The stack is deliberately plain so students can maintain it: **Python, FastAPI, SQLAlchemy, SQLite and server-rendered Jinja2 pages** (no JavaScript build step).

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

`seed-demo` refuses to run if the database already contains users. Do not use demo accounts for real data.

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

## A typical semester

1. **Users** – the organiser creates an account for each student (**Organiser → Users**) and hands over the initial password privately.
2. **Settings** – set the semester name, the target numbers (default 8 sessions: 4 core + 4 optional) and, if needed, the topic categories.
3. **Topics** – create the topic pool (**Organiser → Topics**). Mark each topic *core* (always runs) or *optional* (competes for a slot), and choose its status: *draft* topics stay hidden from students, *active* ones appear in the pool. (The form defaults to *active*; topics created through the API default to *draft*.)
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
- Added: `POST /api/topic-proposals/{id}/archive`, and the optional `ranking` field in the candidate-plan response (every eligible optional topic, best first).
- Added error codes: `AUTH_REQUIRED`, `VALIDATION_ERROR`, `INVALID_CATEGORY`, `INVALID_PROPOSAL_STATE`, `DUPLICATE_PRESENTER`, `PRESENTER_NOT_FOUND`, `USER_NOT_FOUND`, `DUPLICATE_USER`, `LAST_ORGANISER`, `WEAK_PASSWORD`, `CSRF_FAILED`, plus generic `NOT_FOUND`, `CONFLICT` and `INTERNAL_ERROR`.
- `TopicProposal` has an extra nullable `topic_id` linking an approved proposal to the topic it created.
- User and settings management (accounts, passwords, categories) is available through the web pages only.

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
  services/        ALL business rules (preferences, results, planning, sessions, ...)
  api/             thin JSON routers calling the services
  web/             thin HTML routers, Jinja2 templates and CSS calling the same services
  cli.py           init-db, create-user, seed-demo
tests/             pytest suite
```

The rule of thumb: **business rules live in `services/`**; `api/` and `web/` only translate requests and responses. The database also enforces the important invariants itself (one preference per user and topic, one presenter or attendance record per user and session, valid enum values, interest between 0 and 3).

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite covers login, preference creation/update and duplicate prevention, score calculation, candidate ranking, every organiser-only operation, the proposal workflow, sessions and attendance rules, the web forms (including CSRF) and the command-line tools.

## Known limitations

- **No schema migrations.** Tables are created on first start, but later changes to the data model would need a manual migration (or adopting Alembic).
- **No login rate limiting or lockout**, and no email-based password reset. Organisers can set a new password for anyone, but **users cannot change their own password** yet.
- **Logout is client-side.** Sessions are signed cookies, so a copied cookie stays valid until it expires; deactivating the account does cut access immediately.
- **CSRF protection differs by interface.** Web forms carry a per-session token that every POST must present. The JSON API relies on the `SameSite=Lax` session cookie and JSON-only request bodies instead; keep it behind the same origin as the web pages and do not enable cross-origin (CORS) access without adding token checks.
- **Small groups reveal individuals.** Aggregate counts (for example "1 lead presenter") can identify a person when only a few students vote.
- **Last write wins** if the same person edits their preferences in two tabs.
- Changing a presenter's role means removing and re-adding them. Replacing a provisional session's topic clears its presenters.
- Timetable clashes (two sessions in the same week) are not checked, topics and sessions cannot be hard-deleted, and there are no notifications, calendar export or single sign-on (all out of scope for the first release).
- The interface uses plain forms and full page loads; HTMX (suggested in the SRS) is not used.
- Form errors are shown as a message after reloading the page; typed text in a rejected form is not kept.
