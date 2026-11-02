"""Vision package: SAM 3.1 segmentation + Muse Spark label reading.

All model calls go through the passed ``client`` (``MetaClient.responses``);
this package never constructs its own client, so budget, logging, PII
refusal and replay apply. Prompts contain no PII.
"""

from vision.pipeline import run_rack_pipeline  # noqa: F401
from vision.video import select_sharpest_frame  # noqa: F401
