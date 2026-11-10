# Hub Suite CI — Fast drafts, full release gates

CI is split by **PR state**, not by a manually guessed risk label.

| Trigger | Check | PostgreSQL / Docker / Playwright? | Release image? |
| --- | --- | --- | --- |
| Draft PR opened or updated | `Fast draft checks` | No | No |
| PR marked **Ready for review** | `PostgreSQL checks and tests` | Yes — unchanged full suite | No |
| New commits to a ready PR | `PostgreSQL checks and tests` | Yes, on the latest head | No |
| Push to `main` | `PostgreSQL checks and tests` → `publish-release` | Yes | **Yes**, exact tested GHCR digest |
| Manual `workflow_dispatch` | `PostgreSQL checks and tests` | Yes | No (only a successful `main` push publishes) |

## What changes

`.github/workflows/ci.yml` now explicitly listens to `ready_for_review` and `converted_to_draft`. Lightweight draft feedback uses Django system checks, database-free presentation contracts and public menu price calculation tests. No browser install, database provisioning, Docker build, production backup, external connection or publication occurs during draft checks.

The **full job and release-image publication steps are unchanged** and still run on every ready-to-merge PR commit and every `main` push. This retains the same comprehensive test coverage before merging and ensures the GHCR image was built and tested against the actual `main` revision before deployment.

## Operational checks

- Keep the full PostgreSQL job a required pre-merge status check in GitHub branch protection/rulesets. Settings access is controlled separately; this workflow does not change protection policies.
- **Do not merge draft PRs**. For merge readiness, click **Ready for review**, wait for the `PostgreSQL checks and tests` check on the current head to pass, then merge.
- If new commits land after a green full run, wait for the new run on the updated head.
- Merging/pushing to `main` triggers another full run and a tested release-image digest. Deployment uses that exact digest through `scripts/deploy-production.sh` and its production backup/rollback checks; CI never deploys.
- High-risk changes to money, booking, auth or RouterOS should still receive targeted operator/staging smoke tests in addition to the full suite.

## Known constraint

GitHub branch-protection configuration was not readable through the current connector permissions (403), so verify the full check is required from the repository settings. A passing fast draft check **is not** a release approval.
