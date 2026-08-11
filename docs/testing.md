# Testing Hub

Hub supports PostgreSQL only. The complete suite has one entry point:

```bash
scripts/test-full.sh
```

The script refuses to run against any database engine other than PostgreSQL. It
then runs Django's system checks, verifies that model changes have migrations,
and runs the complete Django test suite. Extra arguments are passed to
`manage.py test`; for example, `scripts/test-full.sh --verbosity 2` enables
verbose output without changing the database contract.

## Prerequisites

- Python dependencies from `requirements.txt` are installed.
- A supported PostgreSQL server is running and reachable.
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, and
  `POSTGRES_PORT` identify that server. Their Docker Compose defaults are
  `hub`, `hub`, `hub`, `db`, and `5432` respectively.
- The configured PostgreSQL role can create a test database. Django creates and
  destroys that isolated database during a normal run.

With the repository's Docker environment running, execute the command in the
web container:

```bash
docker compose exec web scripts/test-full.sh
```

For a one-off container (including a fresh database service), use:

```bash
docker compose up -d db
docker compose run --rm web scripts/test-full.sh
```

On a local Python environment, set `POSTGRES_HOST=localhost` (and override the
other `POSTGRES_*` values when necessary) before running the same script.

## Runtime and expected result

Allow approximately **one to three minutes** on a typical development machine,
including creation and migration of the test database. A cold Docker image
build and image download are not part of that estimate. Runtime varies with
host and database performance.

A successful baseline ends with `OK`; the two preceding checks report no
system-check issues and no missing migrations. The suite may report an existing
skip only when its test names the genuinely unavailable external dependency.
No finance rollout flag should be enabled merely for a test run.

## Failure classification

Every failure or error must be assigned one of these categories before it is
changed:

| Category | Required response |
| --- | --- |
| Code bug | Repair the application contract and add or retain regression coverage. |
| Outdated test expectation | Update the assertion to the current contract; do not relax a constraint or permission. |
| Incomplete fixture | Supply the data now required by the production model or workflow. |
| Feature intentionally blocked by an unresolved finance decision | Keep ledger posting disabled and record the unresolved decision; do not invent policy or silently skip the test. |

Infrastructure failures, such as PostgreSQL being unreachable, are not product
test failures and must be fixed in the environment before classification.
Tests must not be deleted or converted to skips to obtain a green run. A skip is
acceptable only for a genuinely unavailable external dependency, and its reason
must say which dependency is unavailable.

When a legacy assertion conflicts with the current application, constraints,
permissions, and the deliberately blocked ledger-writing workflow take
precedence. In particular, stabilization work must not enable ledger posting or
weaken authorization solely to satisfy a test.
