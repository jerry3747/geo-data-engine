# Security Policy

## Supported Versions

The project is under active development. Security fixes are applied to the latest commit on `master` (and the latest release when one exists).

| Version | Supported |
|---|---|
| latest `master` | ✅ |
| older releases | ⚠️ best effort |

## Reporting a Vulnerability

Please **do not open a public issue** for security vulnerabilities. Instead, report privately so the issue can be fixed before disclosure.

We appreciate responsible disclosure. In your report, please include:

- A clear description of the vulnerability and its impact;
- Steps to reproduce (minimal, if possible);
- Suggested fix, if you have one.

We will acknowledge receipt within a reasonable time and keep you informed of progress.

## Security Notes for Users

- **Never commit `worker_config.py`.** It contains your real Redis credentials and server addresses and is gitignored on purpose. Use `worker_config.example.py` as a template and keep secrets in environment variables or your local, untracked config.
- The MCP extension token is read from the environment variable `PLAYWRIGHT_MCP_EXTENSION_TOKEN` — do not hardcode it in source.
- The crawler uses your real logged-in browser sessions. Only run it against platforms you are authorized to access, and be aware that high-frequency automated traffic can trigger account risk controls.
- Collected data may contain sensitive or personal information. Handle and store it in compliance with applicable laws (e.g. personal information protection regulations).
