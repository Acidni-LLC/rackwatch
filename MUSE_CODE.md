# MUSE_CODE.md: task brief for Muse Code

You own two packages: `backend/vision/` and `datagen/`. Work on branch `muse/vision-datagen` and open a PR. Read DESIGN.md §5, §7, §8.1–8.2, §9, §16. Code to `backend/app/schemas.py` (do not edit it; ask for changes in docs/muse-code-log.md).

## Task 1: datagen (do first)
`python datagen/generate.py --seed 42 --size 512 --out data/generated/` produces alerts.jsonl, logs.jsonl, kb/*.md, ground_truth.json, cmdb_seed.json, allowlist.yaml exactly as specified in DESIGN.md §9. Deterministic; all rows validate against schemas.py; RFC 5737 IPs; planted fake PII only.
Done when: `pytest datagen/tests` passes (counts per incident, ≥3 signature logs each, red herrings present, determinism).

## Task 2: vision
`run_rack_pipeline(image_path, client, rack_id, seed_path) -> RackScanResult` and `render_overlay(...)`.
- SAM 3.1 via `client.responses(purpose="sam_segment", model="sam-3.1", input=[{role:user, content:[input_text prompt, input_image data URL]}])`. Parse boxes with the regex in DESIGN.md §5. Prompt sweep + NMS.
- Muse Spark `muse-spark-1.3`, effort low, batched crops, prompts and JSON schema from §8.2. Re-read at high if confidence < 0.6. Deterministic U mapping.
- Video: sharpest of ≤8 frames.
- Use ONLY the passed `client` (never your own OpenAI client).
Done when: on data/rack/sample_rack.jpg, ≥12/14 devices are boxed, the overlay PNG is written, and FakeMetaClient tests pass.

## Logging (required)
Commit trailers: `Agent: muse-code muse-spark-1.3`. Append one entry per session to docs/muse-code-log.md.

## Addenda from M0 and the live preflight (these win over the text above)
Read `docs/decisions.md` and `docs/preflight.json`. Facts confirmed against the live API on Nov 2:

**Setup.** Python 3.11+. `python -m venv .venv && .venv/bin/pip install -r backend/requirements-dev.txt`. `pytest` picks up `pythonpath` from pyproject.toml, so imports are `from app.schemas import ...`, `from app.meta_client import MetaClient`, and `from app.fake_meta_client import FakeMetaClient, fake_sam, fake_text`. Add your tests under `datagen/tests/` and `backend/tests/vision/`. Commit if your sandbox lets you; if `.git` is read-only, leave the changes in the working tree and say so in your final message.

**Datagen.**
- `data/rack/layout.json` is the source of truth for the 14 devices, their ids, serials, IPs and topology. `datagen/scenario.yaml` must match it, and `cmdb_seed.json` is those devices as `Device` rows with `source: "seed"`.
- Two more output files: `sim_state.json` validating against `SimState` (`failed_device_ids: ["dev_A_12"]`, the gateway from layout.json, DNS records including a name served by SRV-DC01), and `contacts.json` as a list of `Contact` (the fake PSA contacts behind the planted PII).
- IPs: infrastructure uses 192.0.2.x and 198.51.100.x. Planted user-laptop IPs use only 203.0.113.x, because the scrubber treats that range as PII (D-004). Do not use 203.0.113.x for anything else.
- Alert ids are `A-0001`.... Log ids are `L-0001`.... Timestamps are on 2026-11-02 UTC, with INC-C from 00:00, INC-B from 01:10 and INC-A from 02:03, all overlapping through about 02:10.
- Every required signature log must fall inside its incident's alert window, and on the root-cause device or a device it affects, because verifier R3 checks window ±30 min and device relevance.
- `backup_failed` must appear in both INC-A (message says "network path not found") and INC-B (message says "no space").

**Vision. SAM 3.1 facts (live).**
- One concept per request. Concept prompts "rack-mounted device", "network switch", "patch panel" and "device" returned **0** boxes on `data/rack/sample_rack.jpg`, and "server" returned 1 box covering the whole rack. "white label" returns one box per sticker.
- Box prompts work: send input_text `<0f>0<|box;x1=..;y1=..;x2=..;y2=..;w=W;h=H|>,1<|box;...|>` with the image, using the image's own pixel W and H. The response gives one tight box per object under your ids. This returned 14/14 on the sample rack.
- Output boxes are in original image pixels. Output text starts with `<0f>`. The regex needs an optional `;c=` group.
- Pipeline: (1) concept prompt per §8.1, keep boxes after the 40%-width filter; (2) if fewer than 4 survive, ask Spark to ground every device, then pass those boxes as one SAM box-prompt request; (3) NMS, sort top to bottom. Grounding prompt that worked: "You are an object grounding expert. Provide the bounding box of every rack-mounted device in this server rack (switches, servers, patch panels, firewall, storage, UPS, PDU, KVM). Do not include empty rack units, blanking panels or the rack itself. Answer as JSON {\"devices\":[{\"label\":\"<sticker text or short name>\",\"bbox\":{\"x_min\":int,\"y_min\":int,\"x_max\":int,\"y_max\":int}}]}. Coordinates are on a 0-1000 grid."

**Vision. Spark facts (live).**
- **Spark image coordinates are on a normalized 0–1000 grid, even if you ask for pixels** (asked for pixels on a 2048 px image, it answered `rack_bottom_y: 976`). Ask for the 0–1000 grid explicitly and convert with the real image size. Clamp to bounds.
- `text={"format":{"type":"json_object"}}` works, and so does strict `json_schema` under `text.format`. Effort levels: minimal, low, medium, high, xhigh, max.
- Call `client.responses(purpose=..., model=..., input=[...], effort="low", max_output_tokens=2000, text={...})`. It returns an object with `.output_text`, `.call_id`, `.usage`. Collect `.call_id` into `RackScanResult.calls`. Model names come from `client.settings.spark_model` and `client.settings.sam_model`.
- Vision prompts must contain no PII, because the client refuses payloads that match PII patterns.

**Live checks.** `MODEL_API_KEY` is in your environment. A live run of `run_rack_pipeline` on the sample rack costs about $0.01: you may run it up to 5 times, using `MetaClient(settings=get_settings(mode="live"), run_id="muse_dev")`. Never print the key, write it to a file, or add a `.env`.

**Scope.** Stop when both "done when" checks pass. Do not refactor anything outside your two packages. Write your session entry in docs/muse-code-log.md. Do not claim a check passed unless you ran it.
