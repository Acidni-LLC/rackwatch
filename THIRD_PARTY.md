# Third-party models, services, and dependencies

## Models (runtime, all Meta)
| Model | Provider / endpoint | Terms |
|---|---|---|
| muse-spark-1.3 | Meta Model API, https://api.meta.ai/v1/responses | Meta Model API terms |
| sam-3.1 | Meta Model API, /v1/responses | Meta Model API terms |
| muse-voice-transcribe-1.0 | Meta Model API, /v1/asr/transcribe | Meta Model API terms |
| (stretch) muse-glimmer-30b LoRA | Fireworks AI | Muse Model License + Fireworks terms |

## Dev tools (not shipped)
Muse Code (Meta): coding agents used to write this code; see docs/muse-code-log.md.

## Referenced (patterns, not copied unless noted)
- meta-models/meta-model-cookbook: alert-fatigue recipe; ASR request format. <!-- license: TODO check repo LICENSE -->
- meta-models/legal-agent-workshop: verifier/console pattern; SAM box-token regex. <!-- license: TODO check repo LICENSE -->

## Python
openai (Apache-2.0), fastapi (MIT), uvicorn (BSD-3), pydantic (MIT), sse-starlette (BSD-3), requests (Apache-2.0), pillow (MIT-CMU), opencv-python-headless (Apache-2.0), rank-bm25 (Apache-2.0), pyyaml (MIT), scipy (BSD-3), pytest (MIT), ruff (MIT)
## JavaScript
react, react-dom (MIT), vite (MIT), typescript (Apache-2.0), tailwindcss (MIT)
## System
ffmpeg (LGPL/GPL, invoked as an external binary, not bundled), SQLite (public domain)
<!-- Regenerate from lockfiles at M7 (pip-licenses / license-checker). -->
