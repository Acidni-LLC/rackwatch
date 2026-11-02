"""Dev-time asset step: turn our rendered rack into a realistic photo with Muse Image ($0.01 per image).

Not a runtime call. The app never generates images; this only produces the committed sample image.

    python scripts/photoreal_rack.py --out data/rack/sample_rack.jpg
"""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.meta_client import MetaClient  # noqa: E402

PROMPT = (
    "Turn this into a realistic smartphone photograph of a real 42U server rack in a small comms closet, "
    "taken straight on with slight lens distortion and uneven overhead light. Real sheet-metal hardware, "
    "real reflections on the bezels, real ethernet ports with some patch cables. "
    "Keep every device in exactly the same position and height, and keep the rack U numbers on the rails. "
    "Keep every white asset sticker with its exact text, character for character. Do not add or remove devices."
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=str(ROOT / "data/rack/sample_rack_render.jpg"))
    parser.add_argument("--out", default=str(ROOT / "data/rack/sample_rack.jpg"))
    parser.add_argument("--prompt", default=PROMPT)
    args = parser.parse_args()
    client = MetaClient(settings=get_settings(mode="live"), run_id="dev_assets")
    url = f"data:image/jpeg;base64,{base64.b64encode(Path(args.image).read_bytes()).decode()}"
    result = client.responses(
        purpose="dev_asset_image",
        model="muse-image-1.0",
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": args.prompt},
                    {"type": "input_image", "image_url": url},
                ],
            }
        ],
        store=False,
        tools=[
            {
                "type": "image_generation",
                "enable_web_search": False,
                "enable_image_search": False,
                "enable_shell": False,
                "size": "1024x1536",
                "output_format": "jpeg",
            }
        ],
    )
    b64 = next(i["result"] for i in result.output if i.get("type") == "image_generation_call")
    out = Path(args.out)
    out.write_bytes(base64.b64decode(b64))
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
