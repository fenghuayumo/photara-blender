"""Read rendered image colors without applying the output transform twice."""
import bpy
import numpy as np


def _rendered_pixels(filepath, width, height):
    """Read saved RGB values, bottom row first (Blender's pixel order)."""
    image = bpy.data.images.load(filepath, check_existing=False)
    try:
        # PNG/JPEG/etc already contain the output view transform. Reading as
        # data preserves these encoded values instead of decoding them to linear.
        image.colorspace_settings.is_data = True
        if tuple(image.size) != (width, height):
            raise ValueError("Image size does not match exported camera")
        pixels = np.empty(len(image.pixels), dtype=np.float32)
        image.pixels.foreach_get(pixels)
        pixels = pixels.reshape(height, width, -1)
        rgb = pixels[:, :, :3].copy()
        valid = np.all(np.isfinite(rgb), axis=2)
        if filepath.lower().endswith(".exr"):
            # COLMAP/PLY store only byte RGB, so encode linear HDR as sRGB.
            # This clips HDR highlights; it does not apply AgX/Filmic a second time.
            rgb = np.clip(rgb, 0, 1)
            rgb = np.where(rgb <= .0031308, 12.92 * rgb,
                           1.055 * np.power(rgb, 1 / 2.4) - .055)
        if pixels.shape[2] >= 4:
            valid &= pixels[:, :, 3] > .99
        return np.clip(rgb, 0, 1), valid
    finally:
        bpy.data.images.remove(image)

