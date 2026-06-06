import io

import numpy as np
from PIL import Image

from idp_preprocess.models.doctamper import DocTamperModel


def test_mock_infer_without_checkpoint(tmp_path):
    model = DocTamperModel(str(tmp_path / "missing.pt"))
    img = Image.new("RGB", (128, 128), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    result = model.infer_bytes(buf.getvalue())
    assert result["predicted_tampered"] is False
    assert result["tamper_ratio"] == 0.0
