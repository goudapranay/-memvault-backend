"""
services/compression.py  –  Image & video compression for MemVault

Images  →  AVIF (Pillow)  |  fallback WebP
Videos  →  H.265/HEVC via ffmpeg subprocess
"""
import io
import hashlib
import logging
import subprocess
import tempfile
import os
from pathlib import Path
from typing import Tuple, Optional

from PIL import Image, ExifTags

logger = logging.getLogger(__name__)

SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/tiff"}
SUPPORTED_VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/mpeg", "video/x-msvideo", "video/3gpp"}


# ─── Image Compression ────────────────────────────────────────────────────────

def compress_image(data: bytes, quality: int = 82) -> Tuple[bytes, str]:
    """
    Compress image to AVIF (best) or WebP.
    Returns (compressed_bytes, new_mime_type).
    """
    img = Image.open(io.BytesIO(data))

    # Fix EXIF orientation
    img = _fix_orientation(img)

    # Convert RGBA → RGB for AVIF
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    # Try AVIF first
    try:
        buf = io.BytesIO()
        img.save(buf, format="AVIF", quality=quality)
        return buf.getvalue(), "image/avif"
    except Exception:
        pass

    # Fallback to WebP
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=quality, method=6)
    return buf.getvalue(), "image/webp"


def _fix_orientation(img: Image.Image) -> Image.Image:
    try:
        exif = img._getexif()
        if exif is None:
            return img
        for tag, val in exif.items():
            if ExifTags.TAGS.get(tag) == "Orientation":
                ops = {3: 180, 6: 270, 8: 90}
                deg = ops.get(val)
                if deg:
                    img = img.rotate(deg, expand=True)
                break
    except Exception:
        pass
    return img


def generate_thumbnail(data: bytes, size: Tuple[int, int] = (400, 400)) -> bytes:
    """Generate a JPEG thumbnail."""
    img = Image.open(io.BytesIO(data))
    img = _fix_orientation(img)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    img.thumbnail(size, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75)
    return buf.getvalue()


# ─── Video Compression ────────────────────────────────────────────────────────

def compress_video(data: bytes, crf: int = 28, original_mime: str = "video/mp4") -> Tuple[bytes, str]:
    """
    Compress video using ffmpeg H.265.
    Returns (compressed_bytes, "video/mp4").
    Falls back to original if ffmpeg not available.
    """
    # Determine input extension
    ext_map = {
        "video/quicktime": ".mov",
        "video/x-msvideo": ".avi",
        "video/mpeg": ".mpeg",
        "video/3gpp": ".3gp",
    }
    in_ext = ext_map.get(original_mime, ".mp4")

    with tempfile.TemporaryDirectory() as tmp:
        in_path = os.path.join(tmp, f"input{in_ext}")
        out_path = os.path.join(tmp, "output.mp4")

        with open(in_path, "wb") as f:
            f.write(data)

        cmd = [
            "ffmpeg", "-y", "-i", in_path,
            "-c:v", "libx265",
            "-crf", str(crf),
            "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=300)

        if result.returncode != 0:
            logger.warning(f"ffmpeg failed: {result.stderr.decode()[:200]} — returning original")
            return data, original_mime

        with open(out_path, "rb") as f:
            return f.read(), "video/mp4"


def extract_video_thumbnail(data: bytes, mime: str = "video/mp4") -> Optional[bytes]:
    """Extract first-frame thumbnail from video using ffmpeg."""
    ext_map = {"video/quicktime": ".mov", "video/x-msvideo": ".avi"}
    in_ext = ext_map.get(mime, ".mp4")

    with tempfile.TemporaryDirectory() as tmp:
        in_path = os.path.join(tmp, f"input{in_ext}")
        out_path = os.path.join(tmp, "thumb.jpg")

        with open(in_path, "wb") as f:
            f.write(data)

        cmd = ["ffmpeg", "-y", "-i", in_path, "-ss", "00:00:01", "-vframes", "1",
               "-vf", "scale=400:-1", out_path]
        result = subprocess.run(cmd, capture_output=True, timeout=60)

        if result.returncode == 0 and os.path.exists(out_path):
            with open(out_path, "rb") as f:
                return f.read()
    return None


# ─── Deduplication ────────────────────────────────────────────────────────────

def compute_perceptual_hash(data: bytes) -> Optional[str]:
    """
    Simple 8x8 average hash for near-duplicate detection.
    Returns hex string or None on failure.
    """
    try:
        img = Image.open(io.BytesIO(data)).convert("L").resize((8, 8), Image.LANCZOS)
        pixels = list(img.getdata())
        avg = sum(pixels) / len(pixels)
        bits = "".join("1" if p >= avg else "0" for p in pixels)
        return hex(int(bits, 2))[2:].zfill(16)
    except Exception:
        return None


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hamming_distance(h1: str, h2: str) -> int:
    """Compare two perceptual hashes. Distance < 10 = likely duplicate."""
    try:
        n1, n2 = int(h1, 16), int(h2, 16)
        xor = n1 ^ n2
        return bin(xor).count("1")
    except Exception:
        return 64
