# RackWatch

> **Pre-existing vs. new:** Everything in this repository was created during the Meta Global AI Developer Hackathon, Nov 2–4, 2026. No pre-existing code is included. Public Meta docs and examples (meta-model-cookbook alert-fatigue recipe, legal-agent-workshop) were used as references and are credited in THIRD_PARTY.md.

Snap a photo of a server rack → get a CMDB. Hit with 500 alerts → get 3 verified incidents with tickets and client emails.

## Meta models used
| Model | Used for |
|---|---|
| `muse-spark-1.3` | label reading, triage agent with tools, effort routing |
| `sam-3.1` | segmenting each rack device |
| `muse-voice-transcribe-1.0` | tech voice notes |
| Muse Code | coding agent used to build vision + datagen (see docs/muse-code-log.md) |

## Quick start
- No key: `make replay` (recorded runs)
- Live: `cp .env.example .env`, add MODEL_API_KEY, `make dev`

## Architecture · Verifier · PII · Results · Cost · How it was built
<!-- fill per DESIGN.md §19 -->

## Data
Synthetic only, no client PII (DATA.md).

## License
MIT for this code (LICENSE). Meta models/APIs are under Meta's terms. See NOTICE.md and SECURITY.md.
