"""Photara native ASFM v4 writer, matching photara/src/sfm/asfm.cpp.

Full (noncompact) v4 records: little endian, uint64 lengths, FNV-1a checksum.
Only geometry-verified multi-image observations become native sparse tracks.
The shared PLY additionally retains points seen in just one image.
"""
import os
import struct
import shutil
from datetime import datetime
import numpy as np
from . import camera_utils


def _pack(fmt, *values):
    return struct.pack("<" + fmt, *values)


def _string(value):
    data = value.encode("utf-8")
    return _pack("Q", len(data)) + data


def _record(data):
    return _pack("Q", len(data)) + data


def write_asfm(path, frames, intrinsics, points):
    cameras, camera_ids = {}, []
    for frame in frames:
        intr = frame.get("intrinsics", intrinsics)
        key = (frame["width"], frame["height"], *(float(intr[k]) for k in ("fx", "fy", "cx", "cy")))
        if key not in cameras:
            cameras[key] = len(cameras)
        camera_ids.append(cameras[key])
    features = [[] for _ in frames]
    tracks = []
    for point in points:
        observations = {int(obs["image_index"]): obs for obs in point.get("observations", [])}
        if len(observations) < 2:
            continue
        pairs = []
        for image_index, obs in sorted(observations.items()):
            if not 0 <= image_index < len(frames):
                raise ValueError("Native point references an invalid image")
            pairs.append((image_index, len(features[image_index])))
            features[image_index].append((obs["x"], obs["y"]))
        pairs = pairs[:255]
        track = _pack("3dQ", *point["position"], len(pairs))
        track += b"".join(_pack("II", *pair) for pair in pairs)
        track += _pack("IB3B", len(pairs), 1, *point["color"])
        tracks.append(_record(track))
    payload = _pack("Q", len(cameras))
    for (width, height, fx, fy, cx, cy), camera_id in cameras.items():
        payload += _record(_pack("III9dBI", camera_id, width, height,
            fx, fy, cx, cy, 0., 0., 0., 0., fx, 1, 0))
    payload += _pack("Q", len(frames))
    for index, frame in enumerate(frames):
        q, _ = camera_utils.matrix_world_to_colmap(frame["matrix_world"])
        rotation = camera_utils.matrix_from_quaternion(q)
        center = np.asarray(frame["matrix_world"], dtype=float)[:3, 3]
        image = _pack("II", index, camera_ids[index])
        # Studio imports against the image directory, while standalone ASFM
        # loads default to the scene directory. Absolute paths work with both.
        image_path = os.path.abspath(os.path.join(os.path.dirname(path), "images", frame["filename"]))
        image += _string(image_path.replace("\\", "/"))
        image += _pack("12d", *rotation.ravel(), *center)
        image += _pack("BII", 1, frame["width"], frame["height"]) + _string("blender_geometry")
        image += _pack("Q", len(features[index]))
        image += b"".join(_pack("5f", x, y, 1., 0., 1.) for x, y in features[index])
        image += _pack("B", 0)  # not_audited; do not claim graph support.
        payload += _record(image)
    payload += _pack("Q", len(tracks)) + b"".join(tracks)
    checksum = 14695981039346656037
    for value in payload:
        checksum = ((checksum ^ value) * 1099511628211) & 0xffffffffffffffff
    with open(path, "wb") as stream:
        stream.write(b"AETHSFM\0" + _pack("IIQQ", 4, 1, len(payload), checksum) + payload)
    return {"sparse_tracks": len(tracks), "ply_points": len(points),
            "single_view_points_in_ply_only": len(points) - len(tracks)}


def repair_image_paths(path, images_dir):
    """Repair existing ASFM path strings only, preserving all other record bytes."""
    with open(path, "rb") as stream:
        data = stream.read()
    if data[:8] != b"AETHSFM\0" or len(data) < 32:
        raise ValueError("Not an ASFM file")
    version, minimum, size, checksum = struct.unpack_from("<IIQQ", data, 8)
    payload = data[32:]
    def digest(body):
        value = 14695981039346656037
        for byte in body:
            value = ((value ^ byte) * 1099511628211) & 0xffffffffffffffff
        return value
    if size != len(payload) or digest(payload) != checksum:
        raise ValueError("Invalid ASFM payload or checksum")
    offset = 0
    def take_count():
        nonlocal offset
        count, = struct.unpack_from("<Q", payload, offset)
        offset += 8
        return count
    for _ in range(take_count()):
        length = take_count()
        offset += length
    images = take_count()
    result = bytearray(payload[:offset])
    changed = 0
    for _ in range(images):
        length = take_count()
        body = payload[offset:offset+length]
        offset += length
        path_size, = struct.unpack_from("<Q", body, 8)
        stored = body[16:16+path_size].decode("utf-8")
        if os.path.isabs(stored) and os.path.isfile(stored):
            resolved = stored
        else:
            relative = stored.replace("\\", "/")
            if relative.startswith("images/"):
                relative = relative[len("images/"):]
            resolved = os.path.abspath(os.path.join(images_dir, relative))
        if not os.path.isfile(resolved):
            raise ValueError("Missing image: " + resolved)
        normalized = resolved.replace("\\", "/")
        changed += stored != normalized
        result.extend(_record(body[:8] + _string(normalized) + body[16+path_size:]))
    result.extend(payload[offset:])  # native track bytes remain untouched.
    if changed:
        backup = str(path) + ".before-path-fix-" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        shutil.copy2(path, backup)
        temporary = str(path) + ".path-fix.tmp"
        with open(temporary, "wb") as stream:
            stream.write(data[:8] + _pack("IIQQ", version, minimum, len(result), digest(result)) + result)
        os.replace(temporary, path)
    return {"images": images, "changed_paths": changed}


def rotate_world(path, rotation):
    """Rotate native world positions and W2C poses, preserving image observations."""
    with open(path, "rb") as stream:
        data = bytearray(stream.read())
    if data[:8] != b"AETHSFM\0":
        raise ValueError("Not an ASFM file")
    size, expected = struct.unpack_from("<QQ", data, 16)
    check = 14695981039346656037
    for value in data[32:]:
        check = ((check ^ value) * 1099511628211) & 0xffffffffffffffff
    if size != len(data)-32 or expected != check:
        raise ValueError("Invalid ASFM payload or checksum")
    rotation = np.asarray(rotation, dtype=float)
    if not np.allclose(rotation.T @ rotation, np.eye(3)) or not np.isclose(np.linalg.det(rotation), 1):
        raise ValueError("Expected a proper world rotation")
    offset = 32
    def count():
        nonlocal offset
        value, = struct.unpack_from("<Q", data, offset)
        offset += 8
        return value
    for _ in range(count()):
        length = count()
        offset += length
    images = count()
    for _ in range(images):
        length = count()
        start = offset
        path_length, = struct.unpack_from("<Q", data, start+8)
        pose_offset = start+16+path_length
        pose = np.array(struct.unpack_from("<12d", data, pose_offset))
        r = pose[:9].reshape(3, 3) @ rotation.T
        center = rotation @ pose[9:]
        struct.pack_into("<12d", data, pose_offset, *r.ravel(), *center)
        offset = start + length
    tracks = count()
    for _ in range(tracks):
        length = count()
        position = rotation @ np.array(struct.unpack_from("<3d", data, offset))
        struct.pack_into("<3d", data, offset, *position)
        offset += length
    if offset != len(data):
        raise ValueError("Unexpected ASFM trailing data")
    checksum = 14695981039346656037
    for value in data[32:]:
        checksum = ((checksum ^ value) * 1099511628211) & 0xffffffffffffffff
    struct.pack_into("<Q", data, 24, checksum)
    temporary = str(path) + ".axes.tmp"
    with open(temporary, "wb") as stream:
        stream.write(data)
    os.replace(temporary, path)
    return {"images": images, "tracks": tracks}
