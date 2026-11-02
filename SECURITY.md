# Security

- No secrets in the repo. Keys live only in a local `.env` (gitignored). `.env.example` has placeholders.
- A pre-commit secret scan (`scripts/secret_scan.py`: detect-secrets plus an exact-value check for the local key) and a CI scan run on every push.
- The API key is passed only to the Meta client and is never logged, stored in the call log, or saved in replay cassettes.
- PII is scrubbed before any model call; the token map stays local.
- Replay/hosted demo runs without any key.
- Report issues: open a GitHub issue (no secrets in it) or email the maintainer listed on the profile.
