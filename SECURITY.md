# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 2.0.x | Yes |
| < 2.0 | Historical / not actively maintained |

This table describes current maintenance focus only. It is not a service-level agreement.

## Security model

- Installed providers are **trusted Python dependencies**.
- Providers are **not sandboxed**.
- Conformance is not a security isolation boundary or security audit.
- The GitHub Action never automatically installs provider packages; caller-prepared trusted runtimes are explicit.
- Credentials and secrets should remain in documented environment mechanisms, not committed files.
- Logging and telemetry must not expose credentials or other secrets.
- Dry-run, explicit `--apply`, and live `--confirm-live` remain explicit safety boundaries.
- No commercial Collibra security or tenant certification is claimed.

Documented trust-model behavior by itself is not a vulnerability.

## Reporting a vulnerability

For ordinary, non-sensitive bugs, use GitHub Issues:

https://github.com/adrianmartnez/collibra-governance-automation/issues

For potentially sensitive security findings:

- do **not** publish credentials, tokens, exploit payloads, or abuse-enabling details in a public issue;
- use GitHub private vulnerability reporting / a Security Advisory when available for this repository;
- if private reporting is unavailable, open only a minimal public issue asking for a private channel, without sensitive details.

## Scope examples

Relevant reports include:

- mutation-authorization bypass;
- unexpected remote writes;
- secret leakage;
- unsafe path traversal or path handling;
- provider trust or loading boundary bugs;
- stale-plan / integrity bypass;
- GitHub Action token-isolation problems.
