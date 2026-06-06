"""DocTamper MiT-B2 + U-Net fraud detector (ported from references/)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn
from PIL import Image
from torchvision import transforms

class DocTamperModel:
    def __init__(
        self,
        checkpoint_path: str,
        encoder_name: str = "mit_b2",
        tamper_threshold: float = 0.0001,
        device: str | None = None,
    ) -> None:
        import segmentation_models_pytorch as smp  # noqa: PLC0415

        self._smp = smp
        self.tamper_threshold = tamper_threshold
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self.model = self._smp.Unet(
            encoder_name=encoder_name,
            encoder_weights=None,
            in_channels=3,
            classes=2,
            activation=None,
        ).to(self.device)
        self._load_checkpoint(checkpoint_path)
        self.model.eval()
        self.normalize = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])
        self.use_amp = self.device.type == "cuda"
        self.amp_dtype = torch.float16

    def _load_checkpoint(self, path: str) -> None:
        ckpt = Path(path)
        if not ckpt.is_file():
            return  # allow running without checkpoint in dev (mock mode)
        try:
            ck = torch.load(ckpt, map_location=self.device, weights_only=False)
        except TypeError:
            ck = torch.load(ckpt, map_location=self.device)
        self.model.load_state_dict(ck["model_state"])

    @property
    def is_loaded(self) -> bool:
        return any(p.abs().sum() > 0 for p in self.model.parameters())

    @torch.no_grad()
    def infer_image(self, image: Image.Image) -> dict[str, Any]:
        if not self.is_loaded:
            return self._mock_result()
        image = image.convert("RGB")
        x = self.normalize(image).unsqueeze(0).to(self.device, non_blocking=True)
        with torch.amp.autocast("cuda", dtype=self.amp_dtype, enabled=self.use_amp):
            logits = self.model(x)
        mask_binary = logits.argmax(dim=1)[0].cpu().numpy().astype(np.uint8)
        prob_fg = torch.softmax(logits, dim=1)[0, 1].cpu().numpy()
        tamper_pixels = int(mask_binary.sum())
        total_pixels = mask_binary.shape[0] * mask_binary.shape[1]
        tamper_ratio = tamper_pixels / max(total_pixels, 1)
        predicted_tampered = tamper_ratio >= self.tamper_threshold
        return {
            "mask_np": mask_binary,
            "prob_map": prob_fg,
            "tamper_ratio": tamper_ratio,
            "tamper_pixels": tamper_pixels,
            "total_pixels": total_pixels,
            "predicted_tampered": predicted_tampered,
        }

    def infer_bytes(self, data: bytes) -> dict[str, Any]:
        return self.infer_image(Image.open(io.BytesIO(data)))

    @staticmethod
    def _mock_result() -> dict[str, Any]:
        return {
            "mask_np": np.zeros((64, 64), dtype=np.uint8),
            "prob_map": np.zeros((64, 64), dtype=np.float32),
            "tamper_ratio": 0.0,
            "tamper_pixels": 0,
            "total_pixels": 64 * 64,
            "predicted_tampered": False,
        }

    def mask_to_png_bytes(self, mask_np: np.ndarray) -> bytes:
        img = Image.fromarray((mask_np * 255).astype(np.uint8), mode="L")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
