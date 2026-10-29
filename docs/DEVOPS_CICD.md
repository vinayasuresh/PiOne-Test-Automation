# PiOne Testmate V2 — Docker CI/CD Guide

## Purpose

The GitHub Actions workflow in `.github/workflows/cicd.yml` protects the `main`
branch before a Docker deployment is triggered. It is intentionally safe for V2:
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

After CI passes for a push to `main`, the Docker CD workflow packages that
same commit, copies it to the server through SSH, and builds/runs the Docker
image on the server.

The browser/live-target suite remains intentionally separate because it needs
Playwright browsers, controlled fixture servers, and authorised targets. It
must not run against customer systems from a public CI runner.

## Configure staging deployment

The `deploy-staging` job runs only after a passing push to `main`. It safely
skips deployment until all required GitHub secrets are configured.

1. In GitHub, open **Settings → Environments** and create `staging`.
2. Add reviewers if staging releases need approval.
3. In **Settings → Secrets and variables → Actions**, add these secrets:

   | Secret | Value |
   | --- | --- |
   | `SERVER_HOST` | Server public IP address or DNS name |
   | `SERVER_USER` | Non-root Linux deployment user |
   | `SERVER_SSH_KEY` | Private SSH key for that deployment user |
   | `SERVER_KNOWN_HOSTS` | Exact `known_hosts` entry for the server |
   | `DEPLOY_PATH` | Absolute server directory, for example `/opt/pione-testmate` |

4. On the server, install Docker Engine with the Compose plugin, create the
   deployment directory, and create `$DEPLOY_PATH/.env` from `.env.example`.
   Keep `DATABASE_URL`, provider keys, and production values only in that
   server-side `.env` file.
5. Ensure the deployment user can run `docker compose` without `sudo`.

The workflow copies the exact commit archive through SSH, starts
`docker-compose.prod.yml`, retains runtime data in Docker named volumes, and
checks `/health` inside the container before declaring the deployment healthy.

## First server preparation

```bash
sudo useradd --create-home --shell /bin/bash pione-deploy
sudo usermod -aG docker pione-deploy
sudo mkdir -p /opt/pione-testmate
sudo chown -R pione-deploy:pione-deploy /opt/pione-testmate
```

Log in as `pione-deploy`, create `/opt/pione-testmate/.env`, and set at least
the variables in `.env.example`. For shared use, configure a PostgreSQL
`DATABASE_URL`; otherwise the compose default persists SQLite in the
`pione_reports` Docker volume.

## Production recommendation

Do not reuse staging credentials for production. Create a separate protected
`production` environment with approvals, database backup, migration review,
health checks, and rollback to the prior release. Store only `DATABASE_URL`,
provider keys, and deployment credentials as GitHub/server secrets—never in
committed `.env` files or generated reports.
