"""Validation of untrusted GLB files before they reach Blender's glTF importer."""
from __future__ import annotations

import json
import struct

MAX_GLB_BYTES = 100 * 1024 * 1024
MAX_JSON_BYTES = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    "KHR_materials_emissive_strength", "KHR_materials_ior", "KHR_materials_specular", "KHR_materials_clearcoat",
    "KHR_materials_transmission", "KHR_materials_volume", "KHR_materials_sheen", "KHR_materials_unlit",
    "KHR_texture_transform", "KHR_mesh_quantization", "KHR_draco_mesh_compression", "EXT_meshopt_compression",
    "KHR_lights_punctual", "KHR_texture_basisu",
}


class MeshRejected(ValueError):
    pass


def validate_glb(data: bytes) -> dict:
    """Return a summary dict, or raise MeshRejected. Pure parsing, nothing is executed."""
    if len(data) < 28 or len(data) > MAX_GLB_BYTES:
        raise MeshRejected("file is empty or larger than 100 MB")
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2:
        raise MeshRejected("not a binary glTF 2.0 (.glb) file")
    if length != len(data):
        raise MeshRejected("GLB length header does not match the file")
    off = 12
    chunk_len, chunk_type = struct.unpack_from("<II", data, off)
    if chunk_type != 0x4E4F534A or chunk_len > MAX_JSON_BYTES or off + 8 + chunk_len > len(data):
        raise MeshRejected("GLB JSON chunk is missing or too large")
    try:
        doc = json.loads(data[off + 8: off + 8 + chunk_len].decode("utf8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise MeshRejected("GLB JSON is invalid") from e
    if not isinstance(doc, dict) or not str(doc.get("asset", {}).get("version", "")).startswith("2"):
        raise MeshRejected("glTF asset version must be 2.x")
    for key in ("buffers", "images"):
        for item in doc.get(key, []) or []:
            uri = item.get("uri") if isinstance(item, dict) else None
            if uri is not None and not str(uri).startswith("data:"):
                raise MeshRejected("external file references are not allowed; embed everything in the .glb")
    unknown = set(doc.get("extensionsRequired", []) or []) - ALLOWED_EXTENSIONS
    if unknown:
        raise MeshRejected(f"unsupported glTF extension: {sorted(unknown)[0]}")
    meshes, nodes = len(doc.get("meshes", []) or []), len(doc.get("nodes", []) or [])
    if meshes == 0:
        raise MeshRejected("the file contains no meshes")
    if meshes > 10_000 or nodes > 100_000:
        raise MeshRejected("scene is too complex")
    return {"meshes": meshes, "nodes": nodes, "materials": len(doc.get("materials", []) or []),
            "generator": str(doc.get("asset", {}).get("generator", ""))[:80]}
