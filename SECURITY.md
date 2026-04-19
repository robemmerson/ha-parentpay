# Security Policy

## Supported Versions

Only the latest `v{MAJOR}.{MINOR}` release receives security fixes. Older versions should upgrade.

## Reporting a Vulnerability

Please **do not** open a public issue for security vulnerabilities.

Use GitHub's private security advisory workflow:

1. Go to <https://github.com/robemmerson/ha-parentpay/security/advisories/new>
2. Fill in the advisory with reproduction steps and suggested fix (if known).

If that's not possible, email the project owner (commit author on the latest release) directly.

## Scope

In scope:
- Code in `custom_components/parentpay/` (the integration itself).
- Data-handling and authentication logic against the ParentPay portal.

Out of scope:
- Vulnerabilities in upstream Home Assistant Core or other custom components.
- Issues in ParentPay's own web application — please report those to ParentPay Ltd directly.
- Transitive vulnerabilities in pinned dev-only dependencies where a fix is blocked upstream
  (tracked in CI `pip-audit` ignore list with rationale).

## Our side of the deal

- CodeQL, gitleaks, bandit, and pip-audit run on every push/PR and on a weekly schedule.
- All GitHub Actions third-party steps are pinned by commit SHA.
- Workflows use least-privilege `permissions:` blocks.
- Dependabot opens weekly update PRs for pip + github-actions.
- Fixtures in `tests/fixtures/` are PII-scrubbed — see the scrub procedure in `CLAUDE.md`.
- Credentials (`PARENTPAY_USERNAME`, `PARENTPAY_PASSWORD`) are read from `.env` locally and
  never committed.
