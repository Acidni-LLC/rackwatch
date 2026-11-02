"""Vision pipeline tests. FakeMetaClient only, $0, no network."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.fake_meta_client import FakeMetaClient, fake_sam, fake_text  # noqa: E402
from vision import prompts as prompts_mod  # noqa: E402
from vision.label_reader import box_to_u, y_to_u  # noqa: E402
from vision.overlay import render_overlay  # noqa: E402
from vision.pipeline import diff_accuracy, run_rack_pipeline  # noqa: E402
from vision.sam import (  # noqa: E402
    box_prompt_text,
    filter_narrow,
    iou,
    nms,
    parse_sam_boxes,
    sort_top_to_bottom,
)
from vision.video import laplacian_sharpness, select_sharpest_frame  # noqa: E402
from vision.video import sample_video_frames  # noqa: E402

W, H = 400, 800


def make_image(path: Path, size: tuple[int, int] = (W, H)) -> str:
    img = Image.new("RGB", size, (60, 60, 70))
    img.save(path, "JPEG")
    return str(path)


def frame_text(top=0, bottom=1000, total=42) -> str:
    return json.dumps({"rack_top_y": top, "rack_bottom_y": bottom, "total_u": total,
                       "rail_numbers_visible": True, "rack_label": "RACK-A"})


def label_text(entries: list[dict]) -> str:
    return json.dumps({"devices": entries})


def entry(crop_id: str, u: int = 12, conf: float = 0.9, **kw) -> dict:
    base = {"crop_id": crop_id, "device_type": "switch", "make": "Cisco",
            "model": "Catalyst 9200L-24P", "serial": "JAE24190LQ2", "hostname": "SW-ACC-02",
            "u_label": u, "u_height": 1,
            "field_confidence": {"make": conf, "model": conf, "serial": conf, "u": conf},
            "evidence": "sticker"}
    return base | kw


def test_parse_sam_boxes_with_confidence_group():
    text = ("<0f>0<|box;x1=10;y1=20;x2=390;y2=100;w=400;h=800;c=0.91|>"
            "<|mask;x=0;y=0;data=1,1,~!|>,1<|box;x1=5;y1=10;x2=195;y2=50;w=200;h=400|>dead")
    assert parse_sam_boxes(text, W, H) == [(10, 20, 390, 100), (10, 20, 390, 100)]
    assert parse_sam_boxes("", W, H) == []
    assert parse_sam_boxes("<0f>0<|box;x1=5;y1=5;x2=5;y2=9;w=400;h=800|>", W, H) == []


def test_nms_iou_filter_and_sort():
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    big, small = (0, 0, 100, 100), (10, 10, 90, 90)
    assert nms([small, big]) == [big]
    assert nms([big, (200, 200, 300, 300)]) == [big, (200, 200, 300, 300)]
    boxes = [(10, 0, 390, 60), (10, 200, 100, 260), (300, 700, 390, 760)]
    assert filter_narrow(boxes, W) == [(10, 0, 390, 60)]  # 40% of 400 = 160
    assert sort_top_to_bottom(list(reversed(boxes))) == boxes
    assert box_prompt_text([(1, 2, 3, 4)], W, H).startswith("<0f>0<|box;")


def _assert_strict_schema(schema: dict, path: str = "root") -> None:
    assert schema.get("type") == "object", path
    assert schema.get("additionalProperties") is False, path
    props = schema.get("properties", {})
    assert set(schema.get("required", [])) >= set(props), path
    for name, sub in props.items():
        if sub.get("type") == "object":
            _assert_strict_schema(sub, f"{path}.{name}")
        if sub.get("type") == "array" and sub.get("items", {}).get("type") == "object":
            _assert_strict_schema(sub["items"], f"{path}.{name}[]")


def test_strict_schemas_satisfy_live_api():
    """The live API rejects strict schemas missing required keys or
    additionalProperties:false (found on live run 1-2)."""
    for schema in (prompts_mod.LABEL_JSON_SCHEMA, prompts_mod.FRAME_JSON_SCHEMA,
                   prompts_mod.GROUNDING_JSON_SCHEMA):
        _assert_strict_schema(schema)


def test_grounding_repair_on_truncated_json():
    from vision.label_reader import ground_devices
    good = json.dumps({"label": "x",
                       "bbox": {"x_min": 0, "y_min": 0, "x_max": 1, "y_max": 1},
                       "devices": [{"label": "sw",
                                     "bbox": {"x_min": 5, "y_min": 5,
                                              "x_max": 95, "y_max": 20}}]})
    client = FakeMetaClient(script=[fake_text('{"devices": [{"label": "sw",'),
                                     fake_text(good)])
    boxes, calls = ground_devices(client, "data:image/jpeg;base64,xx", W, H)
    assert boxes == [(2, 4, 38, 16)]
    assert len(calls) == 2
    assert client.requests[1]["purpose"] == "spark_grounding_repair"


def test_pipeline_concept_path(tmp_path):
    image = make_image(tmp_path / "rack.jpg")
    boxes = [(10, i * 150, 390, i * 150 + 140) for i in range(5)]
    labels = [entry(f"b{i}", u=40 - i * 7) for i in range(5)]
    client = FakeMetaClient(script=[
        fake_sam(boxes, W, H),
        fake_text(frame_text()),
        fake_text(label_text(labels)),
    ])
    result = run_rack_pipeline(image, client, overlay_path=str(tmp_path / "ov.png"))
    assert result.image_w == W and result.image_h == H
    assert len(result.devices) == 5
    assert [d.source for d in result.devices] == ["vision"] * 5
    assert result.calls and len(result.calls) == 3
    assert Path(result.overlay_path).exists()
    purposes = [r["purpose"] for r in client.requests]
    assert purposes == ["sam_segment", "rack_frame", "label_read"]
    assert client.requests[0]["model"] == "sam-3.1"
    assert client.requests[1]["model"] == "muse-spark-1.3"


def test_pipeline_fallback_grounding_then_box_prompt(tmp_path):
    image = make_image(tmp_path / "rack.jpg")
    grounding = {"devices": [
        {"label": "top switch", "bbox": {"x_min": 5, "y_min": 2, "x_max": 95, "y_max": 12}},
        {"label": "bottom ups", "bbox": {"x_min": 5, "y_min": 85, "x_max": 95, "y_max": 97}},
    ]}
    tight = [(20, 16, 380, 96), (20, 680, 380, 776)]
    client = FakeMetaClient(script=[
        fake_sam([], W, H),  # concept prompt finds nothing usable
        fake_text(json.dumps(grounding)),
        fake_sam(tight, W, H),
        fake_text(frame_text()),
        fake_text(label_text([entry("b0", u=40), entry("b1", u=3)])),
    ])
    result = run_rack_pipeline(image, client, overlay_path=str(tmp_path / "ov.png"))
    assert len(result.devices) == 2
    purposes = [r["purpose"] for r in client.requests]
    assert purposes == ["sam_segment", "spark_grounding", "sam_segment",
                        "rack_frame", "label_read"]
    box_text = client.requests[2]["input"][0]["content"][0]["text"]
    assert box_text.startswith("<0f>0<|box;")
    assert "w=400;h=800" in box_text


def test_low_confidence_reread_at_high_effort(tmp_path):
    image = make_image(tmp_path / "rack.jpg")
    boxes = [(10, i * 150, 390, i * 150 + 140) for i in range(4)]
    weak = [entry(f"b{i}", conf=0.95) for i in range(4)]
    weak[0] = entry("b0", conf=0.2, serial=None, make=None, model=None, hostname=None)
    strong = entry("b0", conf=0.95)
    client = FakeMetaClient(script=[
        fake_sam(boxes, W, H),
        fake_text(frame_text()),
        fake_text(label_text(weak)),
        fake_text(label_text([strong])),
    ])
    result = run_rack_pipeline(image, client, overlay_path=str(tmp_path / "ov.png"))
    assert len(result.devices) == 4
    label_reqs = [r for r in client.requests if r["purpose"] == "label_read"]
    assert [r["reasoning"] for r in label_reqs] == [{"effort": "low"}, {"effort": "high"}]
    assert result.devices[0].serial == "JAE24190LQ2"


def test_u_mapping_math():
    assert y_to_u(0, 0, 800, 42) == 42
    assert y_to_u(799, 0, 800, 42) == 1
    assert y_to_u(400, 0, 800, 42) == 21
    start, end = box_to_u((0, 0, 100, 800), 0, 800, 42)
    assert (start, end) == (1, 42)
    start, end = box_to_u((0, 700, 100, 780), 0, 800, 42)
    assert start <= end and start >= 1


def test_accuracy_diff(tmp_path):
    seed = [{"id": "dev_A_12", "u_start": 12, "serial": "JAE24190LQ2"},
            {"id": "dev_A_10", "u_start": 10, "serial": "FGT60FTK21004417"}]
    seed_path = tmp_path / "cmdb_seed.json"
    seed_path.write_text(json.dumps(seed), encoding="utf-8")
    image = make_image(tmp_path / "rack.jpg")
    boxes = [(10, i * 150, 390, i * 150 + 140) for i in range(4)]
    client = FakeMetaClient(script=[
        fake_sam(boxes, W, H),
        fake_text(frame_text()),
        fake_text(label_text([entry(f"b{i}", u=u) for i, u in enumerate([12, 10, 40, 38])])),
    ])
    result = run_rack_pipeline(image, client, seed_path=str(seed_path),
                               overlay_path=str(tmp_path / "ov.png"))
    assert result.accuracy is not None
    assert result.accuracy["devices_total"] == 2
    from app.schemas import BBox, Device
    hand = [Device(id="dev_A_12", rack_id="RACK-A", u_start=12, u_end=12,
                   device_type="switch", serial="JAE24190LQ2",
                   bbox=BBox(x1=0, y1=0, x2=10, y2=10)),
            Device(id="dev_A_99", rack_id="RACK-A", u_start=9, u_end=9, device_type="other",
                   bbox=BBox(x1=0, y1=0, x2=10, y2=10))]
    diff = diff_accuracy(hand, str(seed_path))
    assert diff == {"devices_matched": 1, "devices_total": 2, "serial_matched": 1,
                    "u_matched": 1, "extra_devices": ["dev_A_99"],
                    "missing_devices": ["dev_A_10"]}


def test_overlay_renders_png(tmp_path):
    image = make_image(tmp_path / "rack.jpg")
    out = str(tmp_path / "ov.png")
    render_overlay(image, [{"bbox": {"x1": 10, "y1": 10, "x2": 390, "y2": 100},
                            "device_type": "switch", "u_start": 12,
                            "hostname": "SW-ACC-02"}], out)
    with Image.open(out) as im:
        assert im.size == (W, H)


def test_video_picks_sharpest_frame(tmp_path):
    rng = np.random.default_rng(0)
    sharp = np.zeros((120, 160, 3), dtype=np.uint8)
    sharp[::8, ::8] = 255
    sharp = (sharp + (rng.random((120, 160, 3)) * 40).astype(np.uint8)).astype(np.uint8)
    blurry = cv2.GaussianBlur(sharp, (21, 21), 0)
    assert laplacian_sharpness(sharp) > laplacian_sharpness(blurry)
    assert select_sharpest_frame([blurry, sharp, blurry]) == 1
    video = str(tmp_path / "rack.mp4")
    writer = cv2.VideoWriter(video, cv2.VideoWriter_fourcc(*"mp4v"), 2, (160, 120))
    for frame in (blurry, sharp):
        writer.write(frame)
    writer.release()
    frames = sample_video_frames(video, max_frames=8)
    assert len(frames) == 2
    assert select_sharpest_frame(frames) == 1
