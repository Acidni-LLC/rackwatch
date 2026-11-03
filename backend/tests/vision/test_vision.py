"""Vision pipeline tests. FakeMetaClient only, $0, no network."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.fake_meta_client import FakeMetaClient, fake_sam, fake_text  # noqa: E402
from vision import prompts as prompts_mod  # noqa: E402
from vision.label_reader import box_to_u, combine_confidence, crop_box, y_to_u  # noqa: E402
from vision.overlay import render_overlay  # noqa: E402
from vision.pipeline import (  # noqa: E402
    build_devices,
    diff_accuracy,
    drop_dead_gaps,
    drop_tied_gaps,
    is_implausible_box,
    rail_edges_px,
    rail_x_px,
    resolve_with_flags,
    run_rack_pipeline,
    select_rereads,
)
from vision.railfit import (  # noqa: E402
    Fit,
    assign_units,
    fit_rail,
    frame_fit,
    snap_box,
    theil_sen,
    unit_at,
    unit_coverage,
)
from vision.sam import (  # noqa: E402
    MIN_STICKER_ROWS,
    SAM_MAX_OBJECTS,
    attribute_to_seeds,
    box_gap_seeds,
    box_prompt_text,
    cluster_rows,
    edge_gap_seeds,
    fallback_row_boxes,
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


def test_initial_read_then_capped_reread(tmp_path):
    from vision.label_reader import read_initial, reread_selected
    image = Image.new("RGB", (W, H), (0, 0, 0))
    null = dict(entry("b0"), make=None, model=None, serial=None, hostname=None,
                u_label=None, field_confidence={})
    partial = dict(entry("b1"), model=None, field_confidence={"make": 0.9})
    strong = dict(entry("b1"), field_confidence={"make": 0.95, "model": 0.9,
                                                 "serial": 0.9, "u": 0.9})
    client = FakeMetaClient(script=[fake_text(label_text([null, partial]))])
    merged, calls = read_initial(client, image, [(10, 0, 390, 100), (10, 200, 390, 300)])
    assert len(calls) == 1  # batches only, never a re-read
    assert merged["b0"]["hostname"] is None
    client = FakeMetaClient(script=[fake_text(label_text([strong]))])
    updates, recalls = reread_selected(
        client, image, [("b1", (10, 200, 390, 300))])
    assert len(recalls) == 1
    assert updates["b1"]["model"] == "Catalyst 9200L-24P"


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


def test_theil_sen_ignores_outlier():
    pairs = [(39, 130), (32, 280), (25, 430), (18, 580), (11, 730), (30, 9999.0)]
    line = theil_sen([p[0] for p in pairs], [p[1] for p in pairs])
    assert line is not None
    assert line[1] == -150 / 7  # exact median slope, outlier untouched
    assert theil_sen([5, 5, 5], [1, 2, 3]) is None  # no x spread
    assert theil_sen([], []) is None


def test_fit_rail_outlier_fallback_and_direction():
    pairs = [(39, 130), (32, 280), (25, 430), (18, 580), (11, 730), (30, 9999.0)]
    fit = fit_rail(pairs, 42)
    assert fit is not None and fit.source == "labels"
    assert fit.n_pairs == 6 and fit.n_inliers == 5
    assert not fit.large_residuals
    assert fit_rail(pairs[:3], 42) is None  # too few pairs
    assert fit_rail([(1, 100), (2, 200), (3, 300), (4, 400)], 42) is None  # wrong way up


def test_fit_rail_returns_none_with_too_few_pairs_even_with_frame():
    # caller (build_devices) falls back to the frame rails alone here
    assert fit_rail([(41.0, 500.0)], 42, frame=(100.0, 1900.0)) is None
    fallback = frame_fit(100.0, 1900.0, 42)
    assert fallback.source == "frame"
    assert fallback.a == pytest.approx(1900.0)  # U1 bottom intercept
    assert unit_at(fallback, 1900.0) == 1


def test_fit_rail_arbitration_keeps_exact_line():
    b = -800.0 / 42
    pairs = [(float(x), 800.0 + b * x) for x in (41, 40, 39, 38, 37, 36)]
    fit = fit_rail(pairs, 42, frame=(0.0, 800.0))
    assert fit is not None
    assert unit_at(fit, 800.0 + b * 41) == 41  # exact boundary B(41)
    assert unit_at(fit, 800.0 + b * 36) == 36
    assert assign_units(fit, 800.0 + b * 42, 800.0 + b * 41) == (42, 42)


def test_assign_units_rounds_edges_inward():
    unit = 800 / 42
    fit = Fit(a=800.0, b=-unit, total_u=42, n_pairs=5, n_inliers=5,
              mad_u=0.0, source="labels", large_residuals=False)
    top12, bot12 = 800 - 12 * unit, 800 - 11 * unit
    assert assign_units(fit, top12, bot12) == (12, 12)  # exact 1U box
    assert assign_units(fit, top12, bot12 + 0.1 * unit) == (12, 12)  # 0.1U dip forgiven
    top20, bot20 = 800 - 21 * unit, 800 - 19 * unit
    assert assign_units(fit, top20, bot20) == (20, 21)  # exact 2U box
    assert assign_units(fit, -50, 900) == (1, 42)  # clamped at the rails


def test_build_devices_uses_fit_and_flags_disagreement():
    unit = 800 / 42
    fit = Fit(a=800.0, b=-unit, total_u=42, n_pairs=5, n_inliers=5,
              mad_u=0.0, source="labels", large_residuals=False)
    frame = json.loads(frame_text())
    top12, bot12 = int(800 - 12 * unit), int(800 - 11 * unit)
    boxes = [(0, top12, 399, bot12), (0, top12, 399, bot12)]
    reads = {"b0": entry("b0", u=12), "b1": entry("b1", u=30)}
    devices = build_devices("RACK-A", "img.jpg", boxes, frame, reads, 800, fit)
    assert [d.id for d in devices] == ["dev_A_12", "dev_A_12"]
    assert [d.needs_review for d in devices] == [False, True]
    bad_fit = fit._replace(large_residuals=True)
    flagged = build_devices("RACK-A", "img.jpg", boxes, frame, reads, 800, bad_fit)
    assert all(d.needs_review for d in flagged)


def test_build_devices_falls_back_to_frame_without_fit():
    frame = json.loads(frame_text())
    devices = build_devices("RACK-A", "img.jpg", [(0, 700, 399, 780)], frame, {}, 800)
    assert len(devices) == 1  # empty read still builds from the frame line
    assert devices[0].needs_review  # nothing read: uncertain


def test_drop_dead_gaps_removes_unreadable_and_remaps():
    boxes = [(0, 0, 10, 100), (0, 60, 10, 160), (0, 200, 10, 300)]
    is_gap = [False, True, False]
    reads = {"b0": entry("b0"), "b1": {}, "b2": entry("b2")}
    kept, flags, remapped = drop_dead_gaps(boxes, is_gap, reads)
    assert kept == [(0, 0, 10, 100), (0, 200, 10, 300)]
    assert flags == [False, False]
    assert list(remapped) == ["b0", "b1"]  # row-2 read moved down
    assert remapped["b1"]["crop_id"] == "b2"
    resolved, _ = resolve_with_flags(kept, flags)
    assert resolved[0] == (0, 0, 10, 100)  # dead gap never clipped it
    # a gap box with its own hostname survives
    live = dict(reads)
    live["b1"] = entry("b1")
    assert drop_dead_gaps(boxes, is_gap, live)[0] == boxes


def test_reread_selected_batches_jobs_into_one_call(tmp_path):
    from vision.label_reader import reread_selected
    image = Image.new("RGB", (W, H), (0, 0, 0))
    jobs = [(f"b{i}", (10, i * 150, 390, i * 150 + 100)) for i in range(3)]
    client = FakeMetaClient(script=[
        fake_text(label_text([entry(f"b{i}", conf=0.95) for i in range(3)]))])
    updates, calls = reread_selected(client, image, jobs)
    assert len(calls) == 1  # one shared high-effort call, not three
    assert sorted(updates) == ["b0", "b1", "b2"]
    assert client.requests[0]["reasoning"] == {"effort": "high"}


def test_select_rereads_doubt_first_then_serials_capped():
    fit = Fit(a=800.0, b=-800.0 / 42, total_u=42, n_pairs=8, n_inliers=8,
              mad_u=0.0, source="labels", large_residuals=False)
    boxes = [(0, 700 - 40 * i, 399, 738 - 40 * i) for i in range(8)]
    reads = {}
    for i in range(8):
        lo, _hi = assign_units(fit, boxes[i][1], boxes[i][3])
        reads[f"b{i}"] = dict(entry(f"b{i}", u=lo), serial=None)
    reads["b7"] = dict(entry("b7", u=99), serial="JAE24190LQ2")
    assert select_rereads(boxes, reads, fit) == [7, 0, 1, 2, 3, 4]


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
        fake_sam([], W, H),  # gap fill: nothing hiding between the boxes
        fake_text(label_text([entry(f"b{i}", u=40 - i * 7) for i in range(4)])),
    ])
    result = run_rack_pipeline(image, client, overlay_path=str(tmp_path / "ov.png"))
    assert len(result.devices) == 4
    purposes = [r["purpose"] for r in client.requests]
    assert purposes == ["rack_frame", "sam_segment", "sam_segment", "sam_segment",
                        "label_read"]


def test_pipeline_fallback_grounding_then_box_prompt(tmp_path):
    image = make_image(tmp_path / "rack.jpg")
    tight = [(20, 16, 380, 96), (20, 680, 380, 776)]
    client = FakeMetaClient(script=[
        fake_text(frame_text()),
        fake_sam([], W, H),  # no stickers on this rack
        fake_sam([], W, H),  # concept prompt finds nothing usable
        fake_text(grounding_text([ground_entry("g0", tight[0]), ground_entry("g1", tight[1])])),
        fake_sam(tight, W, H),
        fake_sam([], W, H),  # gap fill between the far-apart boxes finds nothing
        fake_text(label_text([entry("b0", u=40), entry("b1", u=3)])),
    ])
    result = run_rack_pipeline(image, client, overlay_path=str(tmp_path / "ov.png"))
    assert len(result.devices) == 2
    purposes = [r["purpose"] for r in client.requests]
    assert purposes == ["rack_frame", "sam_segment", "sam_segment", "spark_grounding",
                        "sam_segment", "sam_segment", "label_read"]
    box_text = client.requests[4]["input"][0]["content"][0]["text"]
    assert box_text.startswith("<0f>0<|box;")
    assert "w=400;h=800" in box_text


def test_low_confidence_reread_at_high_effort(tmp_path):
    image = make_image(tmp_path / "rack.jpg")
    boxes = [(10, i * 150, 390, i * 150 + 140) for i in range(4)]
    weak = [entry(f"b{i}", u=40 - i * 7, conf=0.95) for i in range(4)]
    weak[0] = entry("b0", u=40, conf=0.2, serial=None, make=None, model=None,
                    hostname=None)
    strong = entry("b0", conf=0.95)
    client = FakeMetaClient(script=[
        fake_text(frame_text()),
        fake_sam([], W, H),  # stickerless test rack -> concept path
        fake_sam(boxes, W, H),
        fake_sam([], W, H),  # gap fill finds nothing new
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
        fake_sam([], W, H),  # gap fill finds nothing new
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


def frame_with_rails(top=0, bottom=1000, left=200, right=800, total=42) -> dict:
    return {"rail_top_y": top, "rail_bottom_y": bottom,
            "rail_left_x": left, "rail_right_x": right, "total_u": total,
            "rail_numbers_visible": True, "rack_label": "RACK-A"}


def test_assign_units_needs_half_coverage():
    unit = 800 / 42
    fit = Fit(a=800.0, b=-unit, total_u=42, n_pairs=5, n_inliers=5,
              mad_u=0.0, source="labels", large_residuals=False)
    top12, bot12 = 800 - 12 * unit, 800 - 11 * unit
    assert assign_units(fit, top12, bot12) == (12, 12)  # exact 1U box
    assert assign_units(fit, top12, bot12 + 0.4 * unit) == (12, 12)  # 0.4U sliver adds no U
    assert assign_units(fit, top12, bot12 + 0.6 * unit) == (11, 12)  # 0.6U overlap counts
    assert assign_units(fit, top12 - 0.4 * unit, bot12) == (12, 12)  # sliver above either side
    top20, bot20 = 800 - 21 * unit, 800 - 19 * unit
    assert assign_units(fit, top20, bot20) == (20, 21)  # exact 2U box
    mid = 800 - 11 * unit  # boundary B(11) between units 11 and 12
    assert assign_units(fit, mid - 0.15 * unit, mid + 0.15 * unit) == (11, 11)  # center fallback
    assert assign_units(fit, -50, 900) == (1, 42)  # clamped at the rails


def test_implausible_box_takes_label_span_and_flags_review():
    unit = 800 / 42
    fit = Fit(a=800.0, b=-unit, total_u=42, n_pairs=8, n_inliers=8,
              mad_u=0.0, source="labels", large_residuals=False)
    frame = frame_with_rails()
    rails = rail_x_px(frame, W)
    assert rails == (80.0, 320.0)
    top12, bot12 = int(800 - 12 * unit), int(800 - 11 * unit)
    top30, bot30 = int(800 - 30 * unit), int(800 - 29 * unit)
    wide = (0, 100, 399, 700)  # runs past the rails onto the wall, ~31U tall
    assert is_implausible_box(wide, fit, 1.0, rails, 0.0, 800.0)
    assert not is_implausible_box((100, top12, 300, bot12), fit, 1.0, rails, 0.0, 800.0)
    boxes = [(100, top12, 300, bot12), (100, top30, 300, bot30), wide]
    reads = {"b0": entry("b0", u=12, u_height=1),
             "b1": entry("b1", u=30, u_height=1),
             "b2": entry("b2", u=20, u_height=2)}
    devices = build_devices("RACK-A", "img.jpg", boxes, frame, reads, H, fit, W)
    assert [d.u_start for d in devices] == [12, 30, 20]
    assert [d.u_end for d in devices] == [12, 30, 21]
    assert [d.id for d in devices] == ["dev_A_12", "dev_A_30", "dev_A_20"]
    assert [d.needs_review for d in devices] == [False, False, True]
    snapped = [(d.bbox.x1, d.bbox.y1, d.bbox.x2, d.bbox.y2) for d in devices]
    assert snapped[0] == (80, top12, 320, bot12)  # y from the fit, x across the rails
    assert snapped[2] == (80, 400, 320, 438)  # span came from the label, then snapped
    for x1, y1, x2, y2 in snapped:  # no box may extend outside the rack frame
        assert 80 <= x1 < x2 <= 320
        assert 0 <= y1 < y2 <= 800


def test_disagreement_keeps_sam_box_visibly_wrong():
    unit = 800 / 42
    fit = Fit(a=800.0, b=-unit, total_u=42, n_pairs=8, n_inliers=8,
              mad_u=0.0, source="labels", large_residuals=False)
    frame = frame_with_rails()
    top12, bot12 = int(800 - 12 * unit), int(800 - 11 * unit)
    sam_box = (100, top12, 300, bot12)
    devices = build_devices("RACK-A", "img.jpg", [sam_box], frame,
                            {"b0": entry("b0", u=30)}, H, fit, W)
    assert devices[0].u_start == 12  # the fit decides, not the bad label
    assert devices[0].needs_review
    kept = (devices[0].bbox.x1, devices[0].bbox.y1,
            devices[0].bbox.x2, devices[0].bbox.y2)
    assert kept == sam_box  # never adjusted to look right when the U disagrees


def test_snap_box_stays_inside_frame():
    unit = 800 / 42
    fit = Fit(a=800.0, b=-unit, total_u=42, n_pairs=5, n_inliers=5,
              mad_u=0.0, source="labels", large_residuals=False)
    assert snap_box(fit, 12, 12, 80.0, 320.0, W, H) == (
        80, int(800 - 12 * unit), 320, int(800 - 11 * unit))
    assert snap_box(fit, 1, 42, 80.0, 320.0, W, H) == (80, 0, 320, 800)
    assert rail_x_px({"rail_top_y": 0}, W) is None  # older frame without rail x
    assert rail_x_px(frame_with_rails(left=900, right=100), W) is None  # degenerate


def test_fallback_row_boxes_cover_orphan_rows_only():
    rows = [[(300, 90, 360, 110)], [(300, 500, 360, 520)]]
    covered = [(10, 50, 390, 150)]
    extra = fallback_row_boxes(rows, covered, W, H, 80.0, 380.0, 0.0, 800.0, 42)
    assert len(extra) == 1  # the second row had no box
    x1, y1, x2, y2 = extra[0]
    assert x1 >= 80 and x2 <= 380 and y1 >= 0 and y2 <= 800
    assert x1 <= 330 <= x2 and y1 <= 510 <= y2  # covers the orphan sticker
    assert fallback_row_boxes(rows, covered + extra, W, H) == []  # no orphans left


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


def test_combine_confidence_type_aware():
    pp = dict(entry("b0", u=42), device_type="patch_panel", make="PANDUIT",
              model=None, serial=None, hostname="PP-01",
              field_confidence={"make": 0.9, "hostname": 0.9, "u": 0.9})
    assert combine_confidence(pp) >= 0.6  # no plate to read: correct, not uncertain
    sw = dict(entry("b0"), model=None,
              field_confidence={"make": 0.9, "serial": 0.9, "hostname": 0.9, "u": 0.9})
    assert combine_confidence(sw) >= 0.6  # model is informational, never required
    noser = dict(entry("b0"), serial=None,
                 field_confidence={"make": 0.9, "model": 0.9, "hostname": 0.9,
                                   "u": 0.9})
    assert combine_confidence(noser) == 0.3  # a switch should have a serial
    nohost = dict(entry("b0"), hostname=None,
                  field_confidence={"make": 0.9, "serial": 0.9, "u": 0.9})
    assert combine_confidence(nohost) == 0.3  # identity missing
    blank = {"crop_id": "b0", "device_type": "blank", "u_label": 25, "u_height": 1,
             "field_confidence": {"u": 0.9}}
    assert combine_confidence(blank) >= 0.6  # a blank needs no fields at all
    assert combine_confidence({}) == 0.0


def exact_1u_box(u: int, h: int = H) -> tuple[int, int, int, int]:
    unit = h / 42
    return (100, int(h - u * unit), 300, int(h - (u - 1) * unit))


def test_fit_rail_bad_fit_threshold():
    uh = 800.0 / 42
    xs = [41, 40, 39, 38, 37]
    loose = [(x, 800.0 - uh * x + e * uh)
             for x, e in zip(xs, [0.4, -0.4, 0.4, -0.4, 0.0], strict=True)]
    fit = fit_rail(loose, 42, frame=(0.0, 800.0))
    assert fit is not None and not fit.large_residuals  # ordinary loose edges
    bad = [(x, 800.0 - uh * x + e * uh)
           for x, e in zip(xs, [1.2, -1.2, 1.2, -1.2, 0.0], strict=True)]
    fit_bad = fit_rail(bad, 42, frame=(0.0, 800.0))
    assert fit_bad is not None and fit_bad.large_residuals  # typically a unit off


def test_build_devices_agreeing_scan_unflagged():
    unit = 800 / 42
    fit = Fit(a=800.0, b=-unit, total_u=42, n_pairs=8, n_inliers=8,
              mad_u=0.2, source="labels", large_residuals=False)
    frame = frame_with_rails()
    boxes = [exact_1u_box(42), exact_1u_box(38), exact_1u_box(12)]
    reads = {
        "b0": dict(entry("b0", u=42), device_type="patch_panel", make="PANDUIT",
                   model=None, serial=None, hostname="PP-01",
                   field_confidence={"make": 0.9, "hostname": 0.9, "u": 0.9}),
        "b1": dict(entry("b1", u=38), hostname="SW-ACC-01", model=None,
                   field_confidence={"make": 0.9, "serial": 0.9, "hostname": 0.9,
                                     "u": 0.9}),
        "b2": entry("b2", u=12),
    }
    devices = build_devices("RACK-A", "img.jpg", boxes, frame, reads, H, fit, W)
    assert [(d.u_start, d.u_end) for d in devices] == [(42, 42), (38, 38), (12, 12)]
    assert [d.needs_review for d in devices] == [False, False, False]


def test_reconcile_shift_by_one_trusted_flagged():
    unit = 800 / 42
    fit = Fit(a=800.0, b=-unit, total_u=42, n_pairs=8, n_inliers=8,
              mad_u=0.2, source="labels", large_residuals=False)
    frame = frame_with_rails()
    devices = build_devices("RACK-A", "img.jpg", [exact_1u_box(41)], frame,
                            {"b0": entry("b0", u=42)}, H, fit, W)
    assert devices[0].u_start == 42  # agreeing reads overrule the loose edge
    assert devices[0].needs_review  # box and reads conflicted
    top42, bot42 = int(800 - 42 * unit), int(800 - 41 * unit)
    kept = (devices[0].bbox.x1, devices[0].bbox.y1,
            devices[0].bbox.x2, devices[0].bbox.y2)
    assert kept == (80, top42, 320, bot42)  # snapped to the trusted span


def test_reconcile_grows_up_when_reads_agree():
    unit = 800 / 42
    fit = Fit(a=800.0, b=-unit, total_u=42, n_pairs=8, n_inliers=8,
              mad_u=0.2, source="labels", large_residuals=False)
    frame = frame_with_rails()
    top20, bot20 = int(800 - 20 * unit), int(800 - 19 * unit)
    box = (100, top20 - int(0.3 * unit), 300, bot20 + int(0.3 * unit))
    assert assign_units(fit, box[1], box[3]) == (20, 20)  # unit 21 lost on coverage
    devices = build_devices("RACK-A", "img.jpg", [box], frame,
                            {"b0": entry("b0", u=20, u_height=2)}, H, fit, W)
    assert (devices[0].u_start, devices[0].u_end) == (20, 21)
    assert not devices[0].needs_review  # label, height and box bottom agree


def test_reconcile_never_shrinks():
    unit = 800 / 42
    fit = Fit(a=800.0, b=-unit, total_u=42, n_pairs=8, n_inliers=8,
              mad_u=0.2, source="labels", large_residuals=False)
    frame = frame_with_rails()
    top31, bot30 = int(800 - 31 * unit), int(800 - 29 * unit)
    devices = build_devices("RACK-A", "img.jpg", [(100, top31, 300, bot30)], frame,
                            {"b0": entry("b0", u=30, u_height=1)}, H, fit, W)
    assert (devices[0].u_start, devices[0].u_end) == (30, 31)  # box keeps its unit
    assert not devices[0].needs_review


def test_implausible_corroborated_span_unflagged():
    unit = 800 / 42
    fit = Fit(a=800.0, b=-unit, total_u=42, n_pairs=8, n_inliers=8,
              mad_u=0.2, source="labels", large_residuals=False)
    frame = frame_with_rails()
    top31, bot30 = int(800 - 31 * unit), int(800 - 29 * unit)
    wide = (0, top31, 399, bot30)  # past the rails, but the span is certain
    assert is_implausible_box(wide, fit, 1.0, rail_x_px(frame, W), 0.0, 800.0)
    devices = build_devices("RACK-A", "img.jpg", [wide], frame,
                            {"b0": entry("b0", u=30, u_height=2)}, H, fit, W)
    assert (devices[0].u_start, devices[0].u_end) == (30, 31)
    assert not devices[0].needs_review  # fit and reads agree despite the box


def test_unit_coverage():
    unit = 800 / 42
    fit = Fit(a=800.0, b=-unit, total_u=42, n_pairs=5, n_inliers=5,
              mad_u=0.0, source="labels", large_residuals=False)
    top12, bot12 = int(800 - 12 * unit), int(800 - 11 * unit)
    assert unit_coverage(fit, top12, bot12, 12) == pytest.approx(1.0, abs=0.05)
    assert unit_coverage(fit, top12, bot12, 11) == 0.0


def test_gap_seed_helpers():
    uh = 800.0 / 42
    seeds = edge_gap_seeds([400.0], W, H, 0.0, 800.0, 42)
    assert seeds  # both rail edges are far from the single row
    assert min(s[1] for s in seeds) < 400 - uh
    assert max(s[3] for s in seeds) > 400 + uh
    for s in seeds:  # every seed stays a full unit clear of the row
        assert s[3] < 400 - uh or s[1] > 400 + uh
    assert edge_gap_seeds([30.0, 770.0], W, H, 0.0, 800.0, 42) == []
    assert edge_gap_seeds([], W, H, 0.0, 800.0, 42) == []
    full_rail = box_gap_seeds([], W, H, 0.0, 800.0, 42)
    assert 0 < len(full_rail) <= SAM_MAX_OBJECTS  # empty scan still seeds the rails
    assert full_rail == sorted(full_rail, key=lambda b: (b[1], b[0]))
    assert box_gap_seeds([(10, 0, 390, 800)], W, H, 0.0, 800.0, 42) == []
    two = [(10, 0, 390, 100), (10, 700, 390, 800)]
    mid = box_gap_seeds(two, W, H, 0.0, 800.0, 42)
    assert len(mid) >= 2  # a wide gap gets spread seeds, not one box
    assert all(100 < (s[1] + s[3]) / 2 < 700 for s in mid)


def test_pipeline_grounding_gap_fill_rescues(tmp_path):
    image = make_image(tmp_path / "rack.jpg")
    tight = [(20, 16, 380, 96), (20, 680, 380, 776)]
    mid_box = (20, 380, 380, 420)
    client = FakeMetaClient(script=[
        fake_text(frame_text()),
        fake_sam([], W, H),  # no stickers on this rack
        fake_sam([], W, H),  # concept prompt finds nothing usable
        fake_text(grounding_text([ground_entry("g0", tight[0]), ground_entry("g1", tight[1])])),
        fake_sam(tight, W, H),
        fake_sam([mid_box], W, H),  # gap fill finds the middle device
        fake_text(label_text([entry("b0", u=40),
                              dict(entry("b1", u=21), hostname="MID-01"),
                              entry("b2", u=3)])),
    # reads key to sorted-box order: tight, mid, tight
    ])
    result = run_rack_pipeline(image, client, overlay_path=str(tmp_path / "ov.png"))
    assert len(result.devices) == 3
    assert "MID-01" in [d.hostname for d in result.devices]
    purposes = [r["purpose"] for r in client.requests]
    assert purposes == ["rack_frame", "sam_segment", "sam_segment", "spark_grounding",
                        "sam_segment", "sam_segment", "label_read"]


def test_pipeline_empty_grounding_seeds_rails(tmp_path):
    image = make_image(tmp_path / "rack.jpg")
    box = (20, 380, 380, 420)
    client = FakeMetaClient(script=[
        fake_text(frame_text()),
        fake_sam([], W, H),  # no stickers on this rack
        fake_sam([], W, H),  # concept prompt finds nothing usable
        fake_text(json.dumps({"devices": []})),  # grounding list is empty too
        fake_sam([box], W, H),  # rail-span seeds still find the device
        fake_text(label_text([dict(entry("b0", u=21), hostname="MID-01")])),
    ])
    result = run_rack_pipeline(image, client, overlay_path=str(tmp_path / "ov.png"))
    assert len(result.devices) == 1
    assert result.devices[0].hostname == "MID-01"
    purposes = [r["purpose"] for r in client.requests]
    assert purposes == ["rack_frame", "sam_segment", "sam_segment", "spark_grounding",
                        "sam_segment", "label_read"]


def test_pipeline_sticker_path_edge_seeds(tmp_path):
    image = make_image(tmp_path / "rack.jpg")
    rows = [[(300, c - 10, 360, c + 10)] for c in (400, 500, 600, 700)]
    stickers = [s for row in rows for s in row]
    tight = [(10, c - 50, 390, c + 50) for c in (400, 500, 600, 700)]
    bare = [dict(entry(f"b{i}"), u_label=None, u_height=None) for i in range(4)]
    client = FakeMetaClient(script=[
        fake_text(frame_text()),
        fake_sam(stickers, W, H),
        fake_sam(tight, W, H),
        fake_text(label_text(bare)),
    ])
    result = run_rack_pipeline(image, client, overlay_path=str(tmp_path / "ov.png"))
    assert len(result.devices) == 4
    prompt = client.requests[2]["input"][0]["content"][0]["text"]
    found = re.findall(r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+)", prompt)
    assert found  # the single box-prompt call carries row, gap and edge seeds
    cys = [(int(y1) + int(y2)) / 2 for _, y1, _, y2 in found]
    assert min(cys) < 350 and max(cys) > 750  # seeds reach past the outer rows
