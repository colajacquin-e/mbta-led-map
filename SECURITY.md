# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly by emailing the maintainer directly rather than opening a public issue.

## Secrets

- MBTA API keys must **never** be committed. Use `.env` files (gitignored) or environment variables.
- The `.env.example` file shows required variables without real values.

## Dependencies

- Keep dependencies up to date. Run `pip audit` periodically to check for known vulnerabilities.
