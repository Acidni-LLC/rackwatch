"""Live smoke test (DESIGN.md §15, about $0.05). Resolves the [U] items in §5 into docs/preflight.json.

Every call goes through MetaClient, so the budget guard and call log apply. The key is never written.

    python scripts/preflight.py [--rack data/rack/sample_rack.jpg] [--wav data/voice/sample_note.wav]
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import re
import struct
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.meta_client import MetaClient  # noqa: E402
from PIL import Image  # noqa: E402

SAM_PROMPTS = [
    "rack-mounted device",
    "network switch",
    "server",
    "patch panel",
    "device label sticker",
    "rack server",
    "rack unit",
    "network device",
    "white label",
]
EFFORTS = ["minimal", "low", "medium", "high", "xhigh", "max"]
BOX = re.compile(r"(\d+)<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)(?:;c=[\d.]+)?\|>")


def probe(results: dict, name: str, fn) -> None:
    started = time.perf_counter()
    try:
        results[name] = {"ok": True, **fn()}
    except Exception as error:  # a preflight records failures rather than stopping at the first one
        results[name] = {"ok": False, "error": f"{type(error).__name__}: {error}"[:500]}
    results[name]["seconds"] = round(time.perf_counter() - started, 2)
    print(
        f"{name:32} {'ok' if results[name]['ok'] else 'FAIL'}  {results[name].get('note', results[name].get('error', ''))}"
    )


def data_url(path: Path) -> tuple[str, int, int]:
    raw = path.read_bytes()
    with Image.open(io.BytesIO(raw)) as im:
        w, h = im.size
    return f"data:image/jpeg;base64,{base64.b64encode(raw).decode()}", w, h


def silence_wav(seconds: int = 3, rate: int = 24000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * rate * seconds)
    return out.getvalue()


def resolved(p: dict) -> dict:
    """The [U] items in DESIGN.md §5, answered from the probes above and from the published docs."""
    sweep = p.get("sam_prompt_sweep", {}).get("sweep", {})
    spaces = {tuple(s) for v in sweep.values() for s in v.get("coordinate_space", [])}
    return {
        "reasoning_effort_levels": p.get("reasoning_effort_levels", {}).get("accepted"),
        "json_schema_strict": p.get("json_schema_strict", {}).get("ok"),
        "tool_choice": "only auto is accepted; none, required and named functions return HTTP 400 (live, Nov 2 2026)",
        "encrypted_reasoning_returned": p.get("tool_call_and_reasoning_replay", {}).get(
            "encrypted_reasoning_returned"
        ),
        "sam_coordinate_space": f"original image pixels, reported as w and h on each box: {sorted(spaces)}",
        "sam_concept_prompts": {k: v.get("boxes") for k, v in sweep.items()},
        "sam_prompt_finding": "device-level concepts find nothing or the whole rack on this image; 'white label' finds each sticker; box prompts return one box per seeded object, up to 16 per request",
        "spark_image_coordinates": "normalized 0-1000 grid, even when pixels are requested (docs/decisions.md D-007)",
        "image_size_limit": "a 1280x1920 JPEG data URL works for Spark and SAM; no smaller limit was hit, and the pipeline caps the longest side at 2048",
        "pricing_usd": "published: $1.25 in, $0.15 cached, $4.25 out per 1M tokens; SAM $2.50 per 1,000 images; voice $0.18 per hour; docs/decisions.md D-006",
        "muse_code": "headless `muse exec`; META_API_KEY in the environment; default model muse-spark-1.2, so runs pass --model muse-spark-1.3",
        "glimmer_lora": "not attempted: the stretch router needs a Fireworks key, which this project does not have",
        "video_as_spark_input": "not tested: video mode runs the photo pipeline on the sharpest frame",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rack", default=str(ROOT / "data/rack/sample_rack.jpg"))
    parser.add_argument("--wav", default=str(ROOT / "data/voice/sample_note.wav"))
    parser.add_argument("--out", default=str(ROOT / "docs/preflight.json"))
    args = parser.parse_args()

    settings = get_settings(mode="live")
    client = MetaClient(settings=settings, run_id="preflight")
    spark = settings.spark_model
    r: dict = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "base_url": settings.base_url,
        "probes": {},
    }
    p = r["probes"]

    def models():
        ids = client.models()
        wanted = [spark, settings.sam_model, settings.asr_model]
        return {
            "models": ids,
            "all_wanted_present": all(m in ids for m in wanted),
            "note": f"{len(ids)} models",
        }

    def plain():
        resp = client.responses(
            purpose="preflight_plain",
            model=spark,
            input="Reply with the word ready.",
            effort="low",
            max_output_tokens=400,
        )
        return {
            "text": resp.output_text[:80],
            "usage": vars(resp.usage),
            "output_types": [i.get("type") for i in resp.output],
            "note": resp.output_text[:40],
        }

    def json_object():
        resp = client.responses(
            purpose="preflight_json_object",
            model=spark,
            input='Return JSON only: {"ok": true}',
            effort="low",
            max_output_tokens=400,
            text={"format": {"type": "json_object"}},
        )
        return {"parsed": json.loads(resp.output_text), "note": resp.output_text[:40]}

    def json_schema():
        resp = client.responses(
            purpose="preflight_json_schema",
            model=spark,
            input="Name one network device type.",
            effort="low",
            max_output_tokens=400,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "device",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "device_type": {"type": "string", "enum": ["switch", "router", "firewall"]}
                        },
                        "required": ["device_type"],
                    },
                }
            },
        )
        return {"parsed": json.loads(resp.output_text), "note": resp.output_text[:40]}

    def tool_call():
        tools = [
            {
                "type": "function",
                "name": "cmdb_lookup",
                "description": "Look up a device in the CMDB.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }
        ]
        messages: list = [
            {"role": "user", "content": "Use cmdb_lookup to find SW-ACC-02, then tell me its U position."}
        ]
        first = client.responses(
            purpose="preflight_tool_1",
            model=spark,
            input=messages,
            tools=tools,
            effort="low",
            max_output_tokens=800,
            store=False,
            include=["reasoning.encrypted_content"],
        )
        calls = first.function_calls
        if not calls:
            raise RuntimeError(f"no function_call: {[i.get('type') for i in first.output]}")
        encrypted = any(i.get("type") == "reasoning" and i.get("encrypted_content") for i in first.output)
        messages += first.output
        messages.append(
            {
                "type": "function_call_output",
                "call_id": calls[0]["call_id"],
                "output": json.dumps({"devices": [{"hostname": "SW-ACC-02", "u_start": 12}]}),
            }
        )
        second = client.responses(
            purpose="preflight_tool_2",
            model=spark,
            input=messages,
            tools=tools,
            effort="low",
            max_output_tokens=800,
            store=False,
            include=["reasoning.encrypted_content"],
        )
        return {
            "args": calls[0].get("arguments"),
            "encrypted_reasoning_returned": encrypted,
            "replay_of_output_items_accepted": True,
            "second_text": second.output_text[:120],
            "second_usage": vars(second.usage),
            "note": f"encrypted={encrypted} answer={second.output_text[:40]!r}",
        }

    def effort_levels():
        accepted, rejected = [], {}
        for level in EFFORTS:
            try:
                client.responses(
                    purpose="preflight_effort",
                    model=spark,
                    input="Say ok.",
                    effort=level,
                    max_output_tokens=300,
                )
                accepted.append(level)
            except Exception as error:
                rejected[level] = str(error)[:160]
        return {"accepted": accepted, "rejected": rejected, "note": f"accepted {accepted}"}

    def sam_sweep():
        url, w, h = data_url(Path(args.rack))
        sweep = {}
        for prompt in SAM_PROMPTS:
            resp = client.responses(
                purpose="preflight_sam",
                model=settings.sam_model,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {"type": "input_image", "image_url": url},
                        ],
                    }
                ],
            )
            boxes = [tuple(int(v) for v in m.groups()) for m in BOX.finditer(resp.output_text)]
            sweep[prompt] = {
                "boxes": len(boxes),
                "coordinate_space": sorted({(b[5], b[6]) for b in boxes}),
                "first": [list(b[1:5]) for b in boxes[:3]],
                "usage": resp.raw.get("usage"),
                "head": resp.output_text[:30],
            }
        return {
            "image_size": [w, h],
            "sweep": sweep,
            "note": ", ".join(f"{k}={v['boxes']}" for k, v in sweep.items()),
        }

    def sam_non_stream():
        url, _, _ = data_url(Path(args.rack))
        resp = client.responses(
            purpose="preflight_sam_nostream",
            model=settings.sam_model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "server"},
                        {"type": "input_image", "image_url": url},
                    ],
                }
            ],
            stream=False,
        )
        return {
            "boxes": len(BOX.findall(resp.output_text)),
            "note": f"non-stream boxes={len(BOX.findall(resp.output_text))}",
        }

    def spark_vision():
        url, w, h = data_url(Path(args.rack))
        resp = client.responses(
            purpose="preflight_spark_vision",
            model=spark,
            instructions="You are reading a photo of a server rack. Return JSON only.",
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_image", "image_url": url},
                        {
                            "type": "input_text",
                            "text": "Return {rack_top_y, rack_bottom_y, total_u, rail_numbers_visible:bool, rack_label:string|null}. Pixel coords in this image.",
                        },
                    ],
                }
            ],
            effort="low",
            max_output_tokens=1500,
            text={"format": {"type": "json_object"}},
        )
        return {
            "parsed": json.loads(resp.output_text),
            "usage": vars(resp.usage),
            "image_size": [w, h],
            "note": resp.output_text[:100],
        }

    def asr_silence():
        result = client.transcribe(
            purpose="preflight_asr", audio=silence_wav(), keywords=["SW-ACC-02", "rack", "U12"]
        )
        return {
            "keys": sorted(result),
            "audioDurationMs": result.get("audioDurationMs"),
            "note": f"silence transcript={result.get('transcript')!r}",
        }

    def asr_speech():
        wav = Path(args.wav)
        if not wav.exists():
            return {"skipped": True, "note": "no sample note yet"}
        result = client.transcribe(
            purpose="preflight_asr", audio=wav.read_bytes(), keywords=["SW-ACC-02", "rack", "U12"]
        )
        return {"result": result, "note": str(result.get("transcript"))[:80]}

    probe(p, "models", models)
    probe(p, "responses_plain", plain)
    probe(p, "json_object", json_object)
    probe(p, "json_schema_strict", json_schema)
    probe(p, "tool_call_and_reasoning_replay", tool_call)
    probe(p, "reasoning_effort_levels", effort_levels)
    probe(p, "sam_prompt_sweep", sam_sweep)
    probe(p, "sam_non_stream", sam_non_stream)
    probe(p, "spark_vision_rack_frame", spark_vision)
    probe(p, "asr_silence", asr_silence)
    probe(p, "asr_speech", asr_speech)

    r["resolved"] = resolved(p)
    row = client.conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(est_cost_usd),0) AS c FROM calls WHERE run_id = 'preflight'"
    ).fetchone()
    r["cost_usd_this_db"] = round(row["c"], 4)
    r["calls_this_db"] = row["n"]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(r, indent=2, default=str).replace(settings.api_key, "<KEY>"), encoding="utf-8")
    print(f"\nwrote {out}; {row['n']} calls, ${row['c']:.4f} est")


if __name__ == "__main__":
    main()
