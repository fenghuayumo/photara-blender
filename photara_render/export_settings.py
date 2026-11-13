"""Format selection shared by the exporter and delivery bridge."""
import os


def selected_format(props):
    # Existing saved scenes keep their old checkbox configuration until the
    # user explicitly chooses a format. New scenes default to COLMAP.
    explicit = getattr(props, "export_format", None)
    if explicit:
        return explicit
    colmap = getattr(props, "export_colmap", True)
    transforms = getattr(props, "export_transforms_json", True)
    return "BOTH" if colmap and transforms else "COLMAP" if colmap else "NERFSTUDIO" if transforms else "NONE"


def dataset_source(props, directory):
    fmt = selected_format(props)
    if fmt == "PHOTARA":
        return os.path.join(directory, "scene.asfm")
    if fmt == "NERFSTUDIO":
        return os.path.join(directory, "transforms.json")
    # Explicit sparse model avoids auto-detection of stale transforms/native files.
    return os.path.join(directory, "sparse", "0")
