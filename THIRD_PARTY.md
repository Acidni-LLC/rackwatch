# Third-party models, services, and dependencies

## Models at runtime (all Meta)
| Model | Endpoint | Terms |
|---|---|---|
| muse-spark-1.3 | Meta Model API, https://api.meta.ai/v1/responses | Meta Model API terms |
| sam-3.1 | Meta Model API, /v1/responses | Meta Model API terms |
| muse-voice-transcribe-1.0 | Meta Model API, /v1/asr/transcribe | Meta Model API terms |

## Used at development time, not shipped and not called by the app
| Tool | Used for |
|---|---|
| Muse Code (Meta) | Wrote `backend/vision/` and `datagen/`; see docs/muse-code-log.md |
| muse-image-1.0 (Meta) | Made the sample rack image photorealistic (`scripts/photoreal_rack.py`) |
| Windows speech synthesis | Generated the sample voice note (`scripts/make_sample_note.ps1`). Not a runtime dependency |
| ffmpeg via imageio-ffmpeg | Local transcoding in dev. Replay needs no ffmpeg; live voice notes need a system ffmpeg or this package |

## Referenced, not copied
- [meta-models/meta-model-cookbook](https://github.com/meta-models/meta-model-cookbook) (MIT, Copyright Meta Platforms, Inc. and affiliates): the ASR multipart request shape in `backend/app/meta_client.py` follows its `06_muse_voice` recipe, and the alert-triage idea follows its alert-fatigue recipe. No files were copied.
- [meta-models/legal-agent-workshop](https://github.com/meta-models/legal-agent-workshop) (no LICENSE file in the repository at the time of writing, so nothing was copied): read for the verifier and console pattern and the scripted fake-model test approach.
- [dev.meta.ai/docs](https://dev.meta.ai/docs): API reference, including the SAM segmentation output grammar that the box regex follows.

## Python, runtime
| Package | Version | License |
|---|---|---|
| openai | 2.54.0 | Apache Software License |
| fastapi | 0.162.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| pydantic | 2.14.2 | MIT |
| sse-starlette | 3.6.0 | BSD-3-Clause |
| requests | 2.34.2 | Apache Software License |
| pillow | 13.0.0 | MIT-CMU |
| opencv-python-headless | 4.14.0.94 | Apache Software License |
| rank-bm25 | 0.2.2 | Apache2.0 |
| PyYAML | 6.0.3 | MIT License |
| scipy | 1.17.1 | BSD License |
| numpy | 2.4.6 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| python-multipart | 0.0.33 | Apache-2.0 |

## Python, development only
| Package | Version | License |
|---|---|---|
| pytest | 9.2.0 | MIT |
| ruff | 0.17.3 | MIT |
| httpx | 0.28.1 | BSD License |
| detect-secrets | 1.5.0 | Apache Software License |
| pip-licenses | | see package |
| imageio-ffmpeg | 0.8.0 | BSD License |

## JavaScript
| Package | Version | License |
|---|---|---|
| @fontsource/chivo | 5.3.0 | OFL-1.1 |
| @fontsource/chivo-mono | 5.3.0 | OFL-1.1 |
| react | 18.3.1 | MIT |
| react-dom | 18.3.1 | MIT |
| @tailwindcss/vite | 4.3.3 | MIT |
| @types/react | 18.3.31 | MIT |
| @types/react-dom | 18.3.7 | MIT |
| @vitejs/plugin-react | 4.7.0 | MIT |
| tailwindcss | 4.3.3 | MIT |
| typescript | 5.9.3 | Apache-2.0 |
| vite | 6.4.5 | MIT |

Chivo and Chivo Mono are by Omnibus-Type, under the SIL Open Font License, and are bundled by `@fontsource`.

## System
SQLite (public domain, via Python's standard library). ffmpeg (LGPL/GPL) is an external binary used only for live voice notes and is not redistributed in this repository.
