# Decisions

Where DESIGN.md was ambiguous, or a live finding changed it. The build instructions ask for the simplest option, noted here.

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
| D-010 | Local dev runs Muse Code in WSL Ubuntu with the sandbox on. The sandbox makes `.git` read-only, so the integrator commits Muse's work on `muse/vision-datagen` with the `Agent: muse-code muse-spark-1.3` trailer. | §16 requires those trailers, and the docs say Muse Code cannot write `.git` from inside its sandbox. |
| D-011 | Secret scanning uses `detect-secrets` plus an exact-value check against the local `.env`, instead of gitleaks. | §17 allows either. This needs no system install and also catches the real key in any file. |
| D-012 | `AGENTS.md` points Muse Code at MUSE_CODE.md. | Muse Code reads `AGENTS.md` first, and the build instructions weren't addressed to it. |
| D-013 | `make` targets wrap `scripts/rw.py`, which also runs directly on Windows. | The README promises `make replay`. Windows dev machines may not have make. |
