# AGENTS.md

You are Muse Code. Your task brief is `MUSE_CODE.md`, and it overrides everything else in this repo.

You own only `backend/vision/` and `datagen/`. Do not edit anything outside them except
`docs/muse-code-log.md` (append your session entry) and, if a schema must change, a request in that log.

## Rules for every agent in this repo
These carried over from the build instructions, retired on Nov 3 (D-021).

1. Every runtime model call is a Meta model: `muse-spark-1.3`, Muse Spark Open, `sam-3.1` or `muse-voice-transcribe-1.0`. No other LLMs, embeddings, OCR or TTS (text-to-speech). Browser speech is fine.
2. All Meta calls go through `backend/app/meta_client.py`, which holds the budget guard, the visible call log and replay.
3. The PII (personally identifiable information) scrubber runs before every model call, and `meta_client` refuses unscrubbed payloads.
4. The verifier is pure Python. The agent can't read ground truth or change the verifier's config.
5. Replay mode works with no key and is the default when `MODEL_API_KEY` is missing.
6. Never commit keys or `.env`, and never log keys. Run `python scripts/secret_scan.py` before every push.
7. The repo is public and holds only work done Nov 2–4, 2026. The README opens with the pre-existing vs. new statement.
8. Budget: stop at $120 total and $1.50 per run. Tests use `FakeMetaClient` and cost nothing.
9. Don't push to `main` without the user's go-ahead. Code freeze is Wed Nov 4, 6:00 PM ET.
10. Muse Code commits carry the trailer `Agent: muse-code muse-spark-1.3`. `scripts/agent_stats.sh` counts them.
