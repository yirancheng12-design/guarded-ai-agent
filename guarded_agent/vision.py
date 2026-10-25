"""ROI pixel evidence and explicit appearance templates, not semantic OCR."""
import hashlib
from .model import Rect


def changed_ratio(before, after, tolerance=24, masks=()):
    if before.size != after.size:
        raise ValueError("ROI geometry changed; images cannot be compared")
    a, b = before.convert("RGB"), after.convert("RGB")
    width, height = a.size
    ignored = bytearray(width * height)
    for r in masks:
        for y in range(max(0, r.top), min(height, r.top + r.height)):
            left, right = max(0, r.left), min(width, r.left + r.width)
            if right > left:
                ignored[y * width + left:y * width + right] = b"\x01" * (right - left)
    usable = len(ignored) - sum(ignored)
    if usable == 0:
        raise ValueError("Masks cover the entire ROI; no visual evidence remains")
    aa, bb = a.tobytes(), b.tobytes()
    changed = 0
    for i, masked in enumerate(ignored):
        if not masked:
            j = i * 3
            if max(abs(aa[j + c] - bb[j + c]) for c in range(3)) > tolerance:
                changed += 1
    return changed / usable


def image_hash(image):
    return hashlib.sha256(str(image.size).encode() + image.convert("RGB").tobytes()).hexdigest()


def cursor_mask(physical_x, physical_y, roi, radius=12):
    return Rect(physical_x - roi.left - radius, physical_y - roi.top - radius, radius * 2 + 1, radius * 2 + 1)
