"""Prompts and JSON schemas for the vision pipeline (DESIGN.md §8.1-8.2).

Spark image coordinates are on a normalized 0-1000 grid (docs/decisions.md
D-007): every prompt that asks for coordinates says so explicitly, and the
pipeline converts with the real image size. Prompts contain no PII.
"""

from __future__ import annotations

CONCEPT_PROMPTS = ["rack-mounted device", "network switch", "server", "patch panel"]

RACK_FRAME_SYSTEM = "You are reading a photo of a server rack. Return JSON only."

RACK_FRAME_PROMPT = (
    "Return {rack_top_y, rack_bottom_y, total_u, rail_numbers_visible:bool, "
    "rack_label:string|null}. "
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
    "Coordinates below are on a normalized 0-1000 grid, not pixels. "
    "Return {devices:[{crop_id, device_type, make, model, serial, hostname, "
    "u_label:int|null, u_height:int, field_confidence:{make, model, serial, u}, evidence}]}. "
    "device_type is one of switch, server, firewall, router, ups, pdu, patch_panel, "
    "storage, kvm, blank, other. u_label is the printed rail number at the bottom "
    "of the device, or null when not legible."
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
        "rack_top_y": {"type": "number"},
        "rack_bottom_y": {"type": "number"},
        "total_u": {"type": "number"},
        "rail_numbers_visible": {"type": "boolean"},
        "rack_label": {"type": ["string", "null"]},
    },
    "required": ["rack_top_y", "rack_bottom_y", "total_u", "rail_numbers_visible", "rack_label"],
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
