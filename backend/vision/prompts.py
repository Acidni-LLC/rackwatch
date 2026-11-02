"""Prompts and JSON schemas for the vision pipeline (DESIGN.md §8.1-8.2).

Spark image coordinates are on a normalized 0-1000 grid (docs/decisions.md
D-007): every prompt that asks for coordinates says so explicitly, and the
pipeline converts with the real image size. Prompts contain no PII.
"""

from __future__ import annotations

CONCEPT_PROMPTS = ["rack-mounted device", "network switch", "server", "patch panel"]

STICKER_PROMPT = "white label"

RACK_FRAME_SYSTEM = "You are reading a photo of a server rack. Return JSON only."

RACK_FRAME_PROMPT = (
    "Return {rail_top_y, rail_bottom_y, rail_left_x, rail_right_x, total_u, "
    "rail_numbers_visible:bool, rack_label:string|null}. "
    "rail_top_y is the y of the top edge of the highest-numbered rack unit, "
    "rail_bottom_y is the y of the bottom edge of unit 1. Read them off the "
    "numbered rails on the left and right sides, not the outer rack frame. "
    "rail_left_x and rail_right_x are the x of the left and right rail "
    "interiors (the uprights the devices mount between). "
    "Coordinates are on a normalized 0-1000 grid over this image "
    "(0,0 is the top-left, 1000,1000 the bottom-right), not pixels."
)

LABEL_SYSTEM = (
    "You read asset labels on IT equipment for an MSP inventory. Report ONLY what is visible. "
    "If a field is not legible, return null. Never infer a serial number. "
    "Brand logos may identify make. "
    "Model may be inferred from front-panel design only if confidence >= 0.7; say so in evidence. "
    "Return JSON only, matching the schema."
)

CROP_PROMPT = (
    "Read each crop (crop_id shown first) plus the full-rack thumbnail for context. "
    "Each crop spans from the left rail to the device's right edge: the rail carries "
    "printed unit numbers, and the device face carries a white asset sticker with "
    "the hostname and serial. "
    "Return {devices:[{crop_id, device_type, make, model, serial, hostname, "
    "u_label:int|null, u_height:int, field_confidence:{make, model, serial, u}, evidence}]}. "
    "device_type is one of switch, server, firewall, router, ups, pdu, patch_panel, "
    "storage, kvm, blank, other. If several labels are visible, report the one "
    "closest to the crop center. u_label is the printed rail number aligned with the "
    "bottom of the device, or null when not legible. "
    "Set a field confidence below 0.6 for anything partly legible, and null for "
    "anything you cannot read."
)

# Grounding prompt that returned usable boxes in the live preflight (MUSE_CODE.md addenda).
GROUNDING_SYSTEM = "You are an object grounding expert. Return JSON only."

GROUNDING_PROMPT = (
    "You are an object grounding expert. Provide the bounding box of every "
    "rack-mounted device in this server rack (switches, servers, patch panels, "
    "firewall, storage, UPS, PDU, KVM). Do not include empty rack units, blanking "
    "panels or the rack itself. Answer as JSON "
    '{"devices":[{"label":"<sticker text or short name>",'
    '"bbox":{"x_min":int,"y_min":int,"x_max":int,"y_max":int}}]}. '
    "Coordinates are on a 0-1000 grid."
)

REREAD_SYSTEM = LABEL_SYSTEM

REREAD_PROMPT = (
    "Re-read this single device crop carefully (plus the full-rack thumbnail). "
    "Return {devices:[{crop_id, device_type, make, model, serial, hostname, "
    "u_label:int|null, u_height:int, field_confidence:{make, model, serial, u}, evidence}]}. "
    "Report ONLY what is visible; null when not legible. Never infer a serial number."
)

LABEL_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "devices": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "crop_id": {"type": "string"},
                    "device_type": {"type": "string"},
                    "make": {"type": ["string", "null"]},
                    "model": {"type": ["string", "null"]},
                    "serial": {"type": ["string", "null"]},
                    "hostname": {"type": ["string", "null"]},
                    "u_label": {"type": ["number", "null"]},
                    "u_height": {"type": "number"},
                    "field_confidence": {"type": "object", "additionalProperties": False},
                    "evidence": {"type": "string"},
                },
                "required": ["crop_id", "device_type", "make", "model", "serial",
                             "hostname", "u_label", "u_height", "field_confidence", "evidence"],
            },
        }
    },
    "required": ["devices"],
}

FRAME_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "rail_top_y": {"type": "number"},
        "rail_bottom_y": {"type": "number"},
        "rail_left_x": {"type": "number"},
        "rail_right_x": {"type": "number"},
        "total_u": {"type": "number"},
        "rail_numbers_visible": {"type": "boolean"},
        "rack_label": {"type": ["string", "null"]},
    },
    "required": ["rail_top_y", "rail_bottom_y", "rail_left_x", "rail_right_x",
                 "total_u", "rail_numbers_visible", "rack_label"],
}

GROUNDING_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "devices": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string"},
                    "bbox": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "x_min": {"type": "number"},
                            "x_max": {"type": "number"},
                            "y_min": {"type": "number"},
                            "y_max": {"type": "number"},
                        },
                        "required": ["x_min", "y_min", "x_max", "y_max"],
                    },
                },
                "required": ["label", "bbox"],
            },
        }
    },
    "required": ["devices"],
}
