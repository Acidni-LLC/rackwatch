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
from vision.label_reader import box_to_u, combine_confidence, crop_box, y_to_u  # noqa: E402
from vision.overlay import render_overlay  # noqa: E402
from vision.pipeline import (  # noqa: E402
    _assign_u,
    build_devices,
    diff_accuracy,
    drop_tied_gaps,
    rail_edges_px,
    run_rack_pipeline,
)
from vision.sam import (  # noqa: E402
    MIN_STICKER_ROWS,
    SAM_MAX_OBJECTS,
    attribute_to_seeds,
    box_prompt_text,
    cluster_rows,
    filter_narrow,
    gap_seed_boxes,
    iou,
    keep_sticker_boxes,
    nms,
    nms_with_flags,
    parse_sam_boxes,
    resolve_overlaps,
    row_seed_boxes,
    sort_top_to_bottom,
    split_multi_row_boxes,
    union_by_sticker,
)
from vision.video import (  # noqa: E402
    laplacian_sharpness,
    sample_video_frames,
    select_sharpest_frame,
)

W, H = 400, 800


def make_image(path: Path, size: tuple[int, int] = (W, H)) -> str:
    img = Image.new("RGB", size, (60, 60, 70))
    img.save(path, "JPEG")
    return str(path)


def frame_text(top=0, bottom=1000, total=42) -> str:
    return json.dumps({"rail_top_y": top, "rail_bottom_y": bottom, "total_u": total,
                       "rail_numbers_visible": True, "rack_label": "RACK-A"})


def sticker_set(centers: list[int]) -> list[tuple[int, int, int, int]]:
    return [(300, c - 10, 360, c + 10) for c in centers]


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


def test_sticker_rows_and_seeds():
    assert MIN_STICKER_ROWS == 4
    stickers = sticker_set([50, 58, 200, 400, 700])
    rows = cluster_rows(stickers, H)
    assert len(rows) == 4  # the 50/58 pair merges
    assert len(rows[0]) == 2
    seeds = row_seed_boxes(rows, W, H)
    assert len(seeds) == 4
    for seed, row in zip(seeds, rows, strict=True):
        cx = sum((b[0] + b[2]) / 2 for b in row) / len(row)
        cy = sum((b[1] + b[3]) / 2 for b in row) / len(row)
        assert seed[0] <= cx <= seed[2] and seed[1] <= cy <= seed[3]  # seed covers its row
        assert seed[2] - seed[0] < W // 2  # narrow: adjacent rows cannot merge
    assert cluster_rows([], H) == []


def test_gap_seeds_split_and_attribute():
    rows = [[(300, 90, 360, 110)], [(300, 500, 360, 520)]]
    gaps = gap_seed_boxes(rows, W, H, 0, H, 42)
    assert len(gaps) == 1
    mid = (gaps[0][1] + gaps[0][3]) / 2
    assert 300 < mid < 320
    close = [[(300, 90, 360, 110)], [(300, 110, 360, 130)]]
    assert gap_seed_boxes(close, W, H, 0, H, 42) == []
    assert gap_seed_boxes(rows, W, H, 0, 0, 42) == []
    assert SAM_MAX_OBJECTS == 16
    wide = [[(300, c, 360, c + 20)] for c in (100, 300, 500, 700)]
    assert len(gap_seed_boxes(wide, W, H, 0, H, 42, max_seeds=2)) == 2
    assert gap_seed_boxes(wide, W, H, 0, H, 42, max_seeds=0) == []
    boxes, flags = attribute_to_seeds([(0, 95, 10, 105), (0, 300, 10, 320)], [100, 305],
                                      [False, True])
    assert flags == [False, True]
    near = [[(300, 90, 360, 110)], [(300, 200, 360, 220)]]
    split = split_multi_row_boxes([(0, 80, 400, 330)], near)
    assert len(split) == 2
    assert split[0][3] <= split[1][1]
    assert split_multi_row_boxes([(0, 80, 400, 120)], near) == [(0, 80, 400, 120)]
    kept, kept_flags = nms_with_flags([(0, 0, 10, 10), (0, 0, 10, 10), (0, 50, 10, 60)],
                                      [True, False, True])
    assert kept == [(0, 0, 10, 10), (0, 50, 10, 60)]
    assert kept_flags == [True, True]


def test_drop_tied_gaps():
    reads = {"b0": {"hostname": "SW-01"}, "b1": {"hostname": "SW-01"},
             "b2": {"hostname": "PP-02"}, "b3": {"hostname": None}}
    out = drop_tied_gaps(reads, [False, True, True, True])
    assert out["b0"] == {"hostname": "SW-01"}
    assert out["b1"] == {}
    assert out["b2"]["hostname"] == "PP-02"
    assert out["b3"] == {"hostname": None}


def test_no_reread_when_read_is_fully_null(tmp_path):
    from vision.label_reader import read_labels
    image = Image.new("RGB", (W, H), (0, 0, 0))
    null = dict(entry("b0"), make=None, model=None, serial=None, hostname=None,
                u_label=None, field_confidence={})
    partial = dict(entry("b1"), model=None, field_confidence={"make": 0.9})
    strong = dict(entry("b1"), field_confidence={"make": 0.95, "model": 0.9,
                                                 "serial": 0.9, "u": 0.9})
    client = FakeMetaClient(script=[fake_text(label_text([null, partial])),
                                     fake_text(label_text([strong]))])
    merged, calls = read_labels(client, image, [(10, 0, 390, 100), (10, 200, 390, 300)])
    assert len(calls) == 2  # batch + one reread for the partial read only
    assert merged["b0"]["hostname"] is None
    assert merged["b1"]["model"] == "Catalyst 9200L-24P"


def test_union_by_sticker_merges_splits_only():
    one = sticker_set([100])
    halves = [(10, 50, 390, 110), (10, 90, 390, 150)]
    assert union_by_sticker(halves, one) == [(10, 50, 390, 150)]
    two = sticker_set([100, 120])
    assert union_by_sticker(halves, two) == halves  # spanning box left alone
    assert union_by_sticker([(0, 0, 10, 10)], []) == [(0, 0, 10, 10)]


def test_keep_sticker_boxes_and_overlap_resolve():
    stickers = sticker_set([100, 300])
    boxes = [(10, 50, 390, 150), (10, 250, 390, 350), (10, 600, 390, 700)]
    assert keep_sticker_boxes(boxes, stickers) == boxes[:2]
    assert resolve_overlaps([(0, 0, 10, 100), (0, 60, 10, 160)]) == [(0, 0, 10, 80), (0, 80, 10, 160)]
    assert resolve_overlaps([(0, 0, 10, 100), (0, 95, 10, 200)]) == [(0, 0, 10, 100), (0, 95, 10, 200)]
    assert resolve_overlaps([(0, 200, 10, 300), (0, 0, 10, 100)]) == [(0, 0, 10, 100), (0, 200, 10, 300)]


def _chain_reads(specs: list[tuple[int | None, int]]) -> dict[str, dict]:
    return {f"b{i}": dict(entry(f"b{i}", u=u if u is not None else 12, u_height=h),
                           u_label=u)
            for i, (u, h) in enumerate(specs)}


def test_bottom_up_support_chain_snaps_floating_2u():
    h = 420
    frame = json.loads(frame_text())
    boxes = [(0, 330, 399, 350), (0, 360, 399, 380), (0, 380, 399, 400), (0, 400, 399, 420)]
    reads = _chain_reads([(7, 2), (4, 2), (3, 2), (1, 1)])
    devices = build_devices("RACK-A", "img.jpg", boxes, frame, reads, h)
    assert [(d.id, d.u_start, d.u_end) for d in devices] == [
        ("dev_A_06", 6, 7), ("dev_A_04", 4, 5), ("dev_A_02", 2, 3), ("dev_A_01", 1, 1)]
    assert [d.needs_review for d in devices] == [True, False, True, False]


def test_assign_u_giant_reanchors_and_gap_falls_back():
    assert _assign_u({"computed": (19, 23), "u_label": 21, "u_height": 1, "tall": True},
                     None) == (20, 21, True)
    assert _assign_u({"computed": (30, 30), "u_label": 30, "u_height": 1, "tall": False},
                     (21, 21)) == (30, 30, False)
    assert _assign_u({"computed": (16, 16), "u_label": 16, "u_height": 1, "tall": False},
                     None) == (16, 16, False)


def test_assign_u_label_beats_geometry_on_disagreement():
    make = {"computed": (15, 17), "u_height": 1, "tall": False}
    assert _assign_u(make | {"u_label": 16}, (12, 12)) == (16, 16, True)
    assert _assign_u(make | {"u_label": 16}, None) == (16, 16, True)
    ups = {"computed": (2, 3), "u_label": 3, "u_height": 2, "tall": False}
    assert _assign_u(ups, (5, 6)) == (2, 3, True)  # gap below: label-top 2U span
    nas = {"computed": (3, 5), "u_label": 4, "u_height": 2, "tall": False}
    assert _assign_u(nas, (2, 3)) == (4, 5, True)  # stack supports label-as-bottom
    assert _assign_u({"computed": (9, 9), "u_label": None, "u_height": 1, "tall": False},
                     None) == (9, 9, True)


def test_rail_edges_and_min_confidence_and_rail_crop(tmp_path):
    assert rail_edges_px(json.loads(frame_text(10, 990)), H) == (8.0, 792.0)
    legacy = {"rack_top_y": 0, "rack_bottom_y": 1000, "total_u": 42}
    assert rail_edges_px(legacy, H) == (0.0, 800.0)
    full = dict(entry("b0"), field_confidence={"make": 0.9, "model": 0.8, "serial": 0.7, "u": 0.95})
    assert combine_confidence(full) == 0.7
    unscored = dict(entry("b0"), field_confidence={})
    assert combine_confidence(unscored) == 0.85  # read but unscored: uncertain, not certain
    gappy = dict(entry("b0"), model=None, serial=None,
                 field_confidence={"make": 0.95, "model": 0.9, "serial": 0.9, "u": 0.9})
    assert combine_confidence(gappy) == 0.3  # nulls pull below review threshold
    assert combine_confidence({}) == 0.0
    image = Image.new("RGB", (W, H), (0, 0, 0))
    assert crop_box(image, (200, 100, 390, 200)).size == (W, 100 + int(100 * 0.08 * 2))
    assert crop_box(image, (200, 100, 390, 200)).size[0] == W  # reaches the left rail


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


def grounding_text(entries: list[dict]) -> str:
    return json.dumps({"devices": entries})


def ground_entry(label: str, box: tuple[int, int, int, int]) -> dict:
    return {"label": label, "bbox": {"x_min": box[0] * 1000 // W, "y_min": box[1] * 1000 // H,
                                    "x_max": box[2] * 1000 // W, "y_max": box[3] * 1000 // H}}


def test_pipeline_sticker_path(tmp_path):
    image = make_image(tmp_path / "rack.jpg")
    stickers = sticker_set([80, 230, 380, 530, 680])
    tight = [(10, 30, 390, 130), (10, 180, 390, 280), (10, 330, 390, 430),
             (10, 480, 390, 580), (10, 630, 390, 730)]
    labels = [entry(f"b{i}", u=40 - i * 7) for i in range(5)]
    client = FakeMetaClient(script=[
        fake_text(frame_text()),
        fake_sam(stickers, W, H),
        fake_sam(tight, W, H),
        fake_text(label_text(labels)),
    ])
    result = run_rack_pipeline(image, client, overlay_path=str(tmp_path / "ov.png"))
    assert result.image_w == W and result.image_h == H
    assert len(result.devices) == 5
    assert [d.source for d in result.devices] == ["vision"] * 5
    assert result.calls and len(result.calls) == 4
    assert Path(result.overlay_path).exists()
    purposes = [r["purpose"] for r in client.requests]
    assert purposes == ["rack_frame", "sam_segment", "sam_segment", "label_read"]
    assert client.requests[1]["model"] == "sam-3.1"
    assert client.requests[0]["model"] == "muse-spark-1.3"
    box_text = client.requests[2]["input"][0]["content"][0]["text"]
    assert box_text.startswith("<0f>0<|box;") and "w=400;h=800" in box_text


def test_pipeline_split_union_and_orphan_rescue(tmp_path):
    image = make_image(tmp_path / "rack.jpg")
    stickers = sticker_set([100, 300, 450, 600])
    split = [(10, 50, 390, 110), (10, 90, 390, 150)]  # two halves, one sticker
    other = [(10, 250, 390, 350)]
    labels = [entry("b0", u=40), entry("b1", u=30)]
    client = FakeMetaClient(script=[
        fake_text(frame_text()),
        fake_sam(stickers, W, H),
        fake_sam(split + other, W, H),
        fake_sam([(10, 550, 390, 650)], W, H),  # orphan row rescue
        fake_text(label_text(labels)),
        fake_text(label_text([entry("b2", u=10)])),
    ])
    result = run_rack_pipeline(image, client, overlay_path=str(tmp_path / "ov.png"))
    assert len(result.devices) == 3  # split merged, orphan rescued
    boxes = [(d.bbox.x1, d.bbox.y1, d.bbox.x2, d.bbox.y2) for d in result.devices]
    assert (10, 50, 390, 150) in boxes
    purposes = [r["purpose"] for r in client.requests]
    assert purposes.count("sam_segment") == 3


def test_pipeline_name_plate_excluded_and_fallback(tmp_path):
    image = make_image(tmp_path / "rack.jpg")
    stickers = sticker_set([5, 400, 700])  # name plate at 5, only two rows below
    boxes = [(10, i * 150, 390, i * 150 + 140) for i in range(4)]
    client = FakeMetaClient(script=[
        fake_text(frame_text(top=20)),  # rail starts below the plate
        fake_sam(stickers, W, H),
        fake_sam(boxes, W, H),  # concept fallback: a stickerless rack
        fake_text(label_text([entry(f"b{i}", u=40 - i * 7) for i in range(4)])),
    ])
    result = run_rack_pipeline(image, client, overlay_path=str(tmp_path / "ov.png"))
    assert len(result.devices) == 4
    purposes = [r["purpose"] for r in client.requests]
    assert purposes == ["rack_frame", "sam_segment", "sam_segment", "label_read"]


def test_pipeline_fallback_grounding_then_box_prompt(tmp_path):
    image = make_image(tmp_path / "rack.jpg")
    tight = [(20, 16, 380, 96), (20, 680, 380, 776)]
    client = FakeMetaClient(script=[
        fake_text(frame_text()),
        fake_sam([], W, H),  # no stickers on this rack
        fake_sam([], W, H),  # concept prompt finds nothing usable
        fake_text(grounding_text([ground_entry("g0", tight[0]), ground_entry("g1", tight[1])])),
        fake_sam(tight, W, H),
        fake_text(label_text([entry("b0", u=40), entry("b1", u=3)])),
    ])
    result = run_rack_pipeline(image, client, overlay_path=str(tmp_path / "ov.png"))
    assert len(result.devices) == 2
    purposes = [r["purpose"] for r in client.requests]
    assert purposes == ["rack_frame", "sam_segment", "sam_segment", "spark_grounding",
                        "sam_segment", "label_read"]
    box_text = client.requests[4]["input"][0]["content"][0]["text"]
    assert box_text.startswith("<0f>0<|box;")
    assert "w=400;h=800" in box_text


def test_low_confidence_reread_at_high_effort(tmp_path):
    image = make_image(tmp_path / "rack.jpg")
    boxes = [(10, i * 150, 390, i * 150 + 140) for i in range(4)]
    weak = [entry(f"b{i}", conf=0.95) for i in range(4)]
    weak[0] = entry("b0", conf=0.2, serial=None, make=None, model=None, hostname=None)
    strong = entry("b0", conf=0.95)
    client = FakeMetaClient(script=[
        fake_text(frame_text()),
        fake_sam([], W, H),  # stickerless test rack -> concept path
        fake_sam(boxes, W, H),
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
        fake_text(frame_text()),
        fake_sam([], W, H),  # stickerless test rack -> concept path
        fake_sam(boxes, W, H),
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
