# Decisions

Where DESIGN.md was ambiguous, or a live finding changed it. The build instructions asked for the simplest option, noted here.

| # | Decision | Why |
|---|---|---|
| D-001 | U12 is SW-ACC-02 (access switch). §2's "core switch SW-CORE-01 in U12" is treated as illustrative. | §9.1 and §20 both name SW-ACC-02 at U12 as the root cause. |
| D-002 | The 14 devices live in `data/rack/layout.json`. `datagen/scenario.yaml` must match it. | §9.1 lists nine and says the layout follows the photo. The render, the seed and the scenario need one source. |
| D-003 | Added `uplink_device_id` to `devices`, and `SimState`, `Contact` plus `sim_state.json` and `contacts.json` to the generator output. | The §8.3 tool table returns `uplink_device_id`. `ping_sim` and `dns_sim` need a failed set they can read without touching ground truth. The scrubber needs the PSA names list from §10. |
| D-004 | Infrastructure IPs use 192.0.2.0/24 and 198.51.100.0/24, which the scrubber allows as lab ranges. User laptop IPs use 203.0.113.0/24, which it scrubs. | §9.3 plants laptop IPs as PII and §9 limits all IPs to RFC 5737. Both hold only if one documentation range counts as non-lab. |
| D-005 | The verifier reads `data/generated/allowlist.yaml`, not `app/verifier/allowlist.yaml`. | §6 and §9.3 disagree. The generator owns the file and the agent can reach neither. |
| D-006 | Live prices replace the proxy: $1.25 in, $0.15 cached, $4.25 out per 1M tokens, SAM $2.50 per 1,000 images, voice $0.18 per hour. | §5 says pricing is unpublished. It is now published at dev.meta.ai/docs/pricing-rate-limits. Standard mode only; Contributor mode allows training on content, so it is not used. |
| D-007 | Spark returns image coordinates on a normalized 0–1000 grid, even when the prompt asks for pixels. | Preflight: asked for pixels on a 2048 px image, it answered `rack_bottom_y: 976`. The image-understanding docs confirm the grid. |
| D-008 | Vision uses Spark grounding plus SAM 3.1 box prompts when concept prompts fail. Concept prompts run first, per §8.1. | Preflight: "rack-mounted device", "network switch" and "patch panel" return 0 boxes on the sample rack; "server" returns the whole rack as one box. Spark grounding plus SAM box prompts returned 14 of 14 tight boxes. |
| D-009 | The sample rack is our own render, made photorealistic with `muse-image-1.0`, with the small sticker text repainted from the layout. It is not a photograph, and DATA.md says so. | §6 allows "a photo we take, or a CC0/our own render". SAM treats the plain render as non-hardware, and Muse Image garbles small text. This is a dev-time asset step; the app makes no image calls at runtime. |
| D-010 | Muse Code runs headless in a WSL Ubuntu clone, with the sandbox on and its own commits on `muse/vision-datagen`. That branch is pushed to this repo and merged with `--no-ff`. | The Windows sandbox can prompt for UAC, which would stall a headless run. A separate clone keeps the key file and this working tree out of its reach. |
| D-011 | Secret scanning uses `detect-secrets` plus an exact-value check against the local `.env`, instead of gitleaks. | §17 allows either. This needs no system install and also catches the real key in any file. |
| D-012 | `AGENTS.md` points Muse Code at MUSE_CODE.md. | Muse Code reads `AGENTS.md` first, and the build instructions weren't addressed to it. |
| D-013 | `make` targets wrap `scripts/rw.py`, which also runs directly on Windows. | The README promises `make replay`. Windows dev machines may not have make. |
| D-014 | `MAX_TOKENS_PER_RUN` counts fresh tokens (uncached input plus output), not all input. | Each turn re-sends the conversation, so cumulative input with cache hits passes 600k at about $0.45, a third of the $1.50 run cap. The dollar cap is the real limit. |
| D-015 | The last turn of an attempt offers only `submit_incidents`. The API accepts only `tool_choice: auto`, so it cannot be forced outright. | Otherwise a rushed attempt can time out without ever reaching the verifier, which hides the failure the design wants to show. |
| D-016 | High-effort turns get `max_output_tokens` 12,000 instead of §14's 4,000. | Reasoning tokens count toward the limit, so 4,000 risks truncating a planning turn. Low-effort turns keep 4,000. |
| D-017 | Muse Spark Open, Meta's open-weight model, runs through an inference partner behind `MetaClient`, as an extra engine (`ENGINE=hosted|open|mixed`). Baseten is the default and Fireworks also works (`OPEN_PROVIDER`). The Glimmer LoRA router in §8.5 v2 is not built. | Self-hosting Spark Open takes at least 700 GB of VRAM (video memory), per its model card. Both partners serve it with the same Responses API and prices, and the PII guard, budget guard, call log and replay stay shared. Hard rule 2 names the Meta base URL; these are the only other endpoints, and they serve only a Meta model. A LoRA needs training time and a paid deployment this schedule lacks. |
| D-018 | `hosted` stays the default engine, with open weights one click away. The `mixed` engine stays in the code but is not recommended. | `muse-spark-1.3` is the model DESIGN.md builds on, and a key for it is all a live run needs. In 5 runs each, `open` averaged $0.18 and 38 s against $0.30 and 58 s for `hosted`, at the same F1, and `mixed` averaged $0.37 and 138 s because 70 of its 86 turns ran at high effort on the hosted model. |
| D-019 | The rack scan is scored on whole U spans (`span_matched`) as well as the pipeline's `u_matched`, and the UI shows the span score. | `u_matched` compares only the bottom unit, so it calls a 2U server at U20-20 correct. The pipeline is Muse Code's, so the integrator's score lives in `app/rack.py`. |
| D-020 | Overlay boxes are snapped to the fitted U grid after SAM finds the devices, except a box whose U disagrees with its own label, which keeps its SAM box. | SAM boxes on this rack hugged the label stickers and one ran past the rack frame. Snapping shows the decided position, and a wrong U still looks wrong. |
| D-021 | The build instructions file is retired on Nov 3. Its rules that still apply moved into `AGENTS.md`. | The build it ordered is done, and Muse Code warned that it ignores that file when `AGENTS.md` sits beside it. One rules file keeps every agent on the same rules. |
| D-022 | The hosted demo is replay-only, with no keys in the image or the app's settings. | It's a public URL, so a live key would let any visitor spend the budget. Recorded runs still show the whole flow, and the tools and verifier run for real. |
| D-023 | Muse Code sessions 2 to 5 could run 8 or 9 live scans, above the 5 in its brief. | The integrator set that limit in each session's prompt, because the bug being fixed was scan-to-scan variance, which takes repeat scans to measure. Scans cost $0.07 to $0.12 each, inside the $120 budget. |

## Glossary
| Term | Meaning |
|---|---|
| API | Application programming interface |
| CC0 | Creative Commons Zero, a public domain dedication |
| F1 | The harmonic mean of precision and recall |
| LoRA | Low-rank adaptation, a way to fine-tune a model |
| PII | Personally identifiable information |
| PSA | Professional services automation |
| RFC | Request for Comments, an internet standards document |
| SAM | Segment Anything Model |
| U | Rack unit, the standard height of rack equipment |
| UAC | User Account Control, the Windows permission prompt |
| VRAM | Video memory |
| WSL | Windows Subsystem for Linux |
