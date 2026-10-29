# PiOne Testmate V2 — CI/CD Guide

## Purpose

The GitHub Actions workflow in `.github/workflows/ci.yml` protects the `main`
branch before a deployment is triggered. It is intentionally safe for V2:
it does not scan external sites, use production credentials, or perform an
automatic production release.

## Technology stack

| Area | Technology |
| --- | --- |
| API | Python 3.11+, FastAPI, Uvicorn |
| UI | Static HTML, CSS, and JavaScript served by FastAPI |
| Browser automation | Playwright Chromium |
| Persistence | SQLAlchemy; SQLite for local use, PostgreSQL through `DATABASE_URL` for shared environments |
| Schema management | Alembic |
| Exports | Pandas and OpenPyXL |
| AI planning | Deterministic grounded planner, optional Google Gemini or local Ollama |
| Test runner | Pytest, pytest-asyncio, pytest-xdist, pytest-timeout |

## What CI does

On every pull request to `main` and every push to `main`, CI:

1. installs the locked application dependencies;
2. compiles `api/` and `automation_framework/`;
3. runs the default V2 Pytest suite (browser-marked tests stay excluded by
   `pytest.ini`);
4. uploads JUnit test evidence; and
5. produces a source archive tied to the exact Git commit.

The browser/live-target suite remains intentionally separate because it needs
Playwright browsers, controlled fixture servers, and authorised targets. It
must not run against customer systems from a public CI runner.

## Configure staging deployment

The `deploy-staging` job runs only after a passing push to `main` and only
when the repository secret `STAGING_DEPLOY_WEBHOOK_URL` is configured.

1. In GitHub, open **Settings → Environments** and create `staging`.
2. Add reviewers if staging releases need approval.
3. In **Settings → Secrets and variables → Actions**, add the repository or
   staging environment secret `STAGING_DEPLOY_WEBHOOK_URL`.
4. Point it to the internal deployment service webhook owned by DevOps.

The webhook receives the repository name, commit SHA, and `staging`
environment label. It should deploy that exact source bundle or Git SHA,
apply Alembic migrations, and run a health check at `/system/db-status`.

## Production recommendation

Do not reuse the staging secret for production. Create a separate manual
production workflow or a protected `production` environment with approvals,
database backup, migration review, health checks, and rollback to the prior
commit. Store only `DATABASE_URL`, provider keys, and deployment credentials
as GitHub environment secrets—never in `.env` files or committed reports.
