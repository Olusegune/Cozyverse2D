"""Run inside Blender:  blender --background --factory-startup --python render_view.py -- <args>

One invocation == one render call == one PNG (or one GLB). Never renders a sheet.
"""
import argparse
import json
import math
import os
import sys
import time
import uuid

import bpy
import bmesh
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import cv_camera  # noqa: E402
import proxy_spec  # noqa: E402


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--spec", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--meta", required=True)
    p.add_argument("--mode", choices=["render", "mask", "glb"], default="render")
    p.add_argument("--view", default="front")
    p.add_argument("--policy", default="none")
    p.add_argument("--include", default="", help="comma separated part classes; default from policy")
    p.add_argument("--engine", choices=["CYCLES", "WORKBENCH", "EEVEE"], default="CYCLES")
    p.add_argument("--res", type=int, default=1024)
    p.add_argument("--samples", type=int, default=64)
    p.add_argument("--bg", choices=["white", "transparent"], default="white")
    p.add_argument("--quadrant", default="front_left")
    p.add_argument("--elevation", type=float, default=30.0)
    p.add_argument("--focal", type=float, default=50.0)
    p.add_argument("--flat", action="store_true", help="flat unlit colours (test fixtures)")
    p.add_argument("--render-call-id", default="")
    return p.parse_args(argv)


def srgb_to_linear(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def lin(h):
    r, g, b = hex_rgb(h)
    return (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b))


_mat_cache = {}


def make_material(color_rgb255, emission, flat):
    key = (color_rgb255, round(emission, 3), flat)
    if key in _mat_cache:
        return _mat_cache[key]
    mat = bpy.data.materials.new(f"cv_{len(_mat_cache)}")
    r, g, b = (srgb_to_linear(c) for c in color_rgb255)
    mat.diffuse_color = (r, g, b, 1.0)  # Workbench / viewport colour
    if mat.node_tree is None:
        mat.use_nodes = True
    nt = mat.node_tree
    if nt is not None:
        bsdf = nt.nodes.get("Principled BSDF")
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
            bsdf.inputs["Roughness"].default_value = 0.6
            if emission > 0 and not flat:
                bsdf.inputs["Emission Color"].default_value = (r, g, b, 1.0)
                bsdf.inputs["Emission Strength"].default_value = emission
    _mat_cache[key] = mat
    return mat


FACE_AXES = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0),
             "+z": (0, 0, 1), "-z": (0, 0, -1)}


def build_part(part, color_override=None, flat=False):
    shape = part["shape"]
    bm = bmesh.new()
    if shape == "box":
        bmesh.ops.create_cube(bm, size=1.0)
    elif shape == "cylinder":
        bmesh.ops.create_cone(bm, cap_ends=True, segments=48, radius1=0.5, radius2=0.5, depth=1.0)
    elif shape == "sphere":
        bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=0.5)
    elif shape == "prism":   # triangular roof: ridge along Y
        v = [bm.verts.new(c) for c in ((-0.5, -0.5, -0.5), (0.5, -0.5, -0.5), (0.0, -0.5, 0.5),
                                       (-0.5, 0.5, -0.5), (0.5, 0.5, -0.5), (0.0, 0.5, 0.5))]
        for idx in ((0, 2, 1), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5)):
            bm.faces.new([v[i] for i in idx])
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    else:
        raise ValueError(shape)
    sx, sy, sz = part["size"]
    bmesh.ops.scale(bm, vec=(sx, sy, sz), verts=bm.verts)
    mesh = bpy.data.meshes.new(part["name"])
    base_rgb = color_override or hex_rgb(part["color"])
    mats = [make_material(base_rgb, part.get("emission", 0.0), flat)]
    face_colors = part.get("face_colors") if not color_override else None
    if face_colors:
        order = {}
        for f in bm.faces:
            n = f.normal
            axis = max(range(3), key=lambda i: abs(n[i]))
            key = ("+" if n[axis] > 0 else "-") + "xyz"[axis]
            col = face_colors.get(key)
            if col:
                if col not in order:
                    order[col] = len(mats)
                    mats.append(make_material(hex_rgb(col), 0.0, flat))
                f.material_index = order[col]
    bm.to_mesh(mesh)
    bm.free()
    for m in mats:
        mesh.materials.append(m)
    obj = bpy.data.objects.new(part["name"], mesh)
    obj.location = Vector(part["center"])
    if "rotate_y_deg" in part:
        obj.rotation_euler = (0.0, math.radians(part["rotate_y_deg"]), 0.0)
    if "rotate_deg" in part:
        obj.rotation_euler = tuple(math.radians(a) for a in part["rotate_deg"])
    obj["cv_class"] = part["class"]
    bpy.context.scene.collection.objects.link(obj)
    return obj


def import_mesh_part(part, color_override=None, flat=False):
    """Import a validated GLB, apply yaw / target height, and put the bottom centre of its bounds at the origin."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=part["mesh_path"])
    new = [o for o in bpy.data.objects if o not in before]
    for o in new:                       # flatten parenting, keeping world transforms
        mw = o.matrix_world.copy()
        o.parent = None
        o.matrix_world = mw
    meshes = [o for o in new if o.type == "MESH"]
    for o in new:
        if o.type != "MESH":
            bpy.data.objects.remove(o, do_unlink=True)
    if not meshes:
        raise RuntimeError("imported file has no mesh objects")
    bpy.context.view_layer.update()
    lo, hi = world_bounds(meshes)
    cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
    yaw = math.radians(part.get("yaw_deg", 0))
    scale = 1.0
    if part.get("target_height"):
        h = hi[2] - lo[2]
        scale = part["target_height"] / h if h > 1e-9 else 1.0
    rot = Matrix.Rotation(yaw, 4, "Z")
    centre = Matrix.Translation((-cx, -cy, -lo[2]))
    xf = Matrix.Scale(scale, 4) @ rot @ centre
    for o in meshes:
        o.matrix_world = xf @ o.matrix_world
        o["cv_class"] = part["class"]
        if color_override is not None or flat:
            o.data.materials.clear()
            o.data.materials.append(make_material(color_override or (200, 200, 200), 0.0, True))
    return meshes


def world_bounds(objs):
    pts = []
    for o in objs:
        mw = o.matrix_world
        pts.extend(tuple(mw @ Vector(c)) for c in o.bound_box)
    return cv_camera.bounds_of(pts)


def setup_world(scene, args):
    world = bpy.data.worlds.new("cv_world")
    scene.world = world
    world.color = (1.0, 1.0, 1.0)
    if args.engine == "WORKBENCH" or args.mode == "mask":
        return
    if world.node_tree is None:
        world.use_nodes = True
    nt = world.node_tree
    if nt is None:
        return
    nt.nodes.clear()
    lp = nt.nodes.new("ShaderNodeLightPath")
    cam_bg = nt.nodes.new("ShaderNodeBackground")
    amb_bg = nt.nodes.new("ShaderNodeBackground")
    mix = nt.nodes.new("ShaderNodeMixShader")
    out = nt.nodes.new("ShaderNodeOutputWorld")
    cam_bg.inputs["Color"].default_value = (1, 1, 1, 1)
    cam_bg.inputs["Strength"].default_value = 1.0   # camera sees pure white
    amb_bg.inputs["Color"].default_value = (1, 1, 1, 1)
    amb_bg.inputs["Strength"].default_value = 0.6   # neutral ambient fill
    nt.links.new(lp.outputs["Is Camera Ray"], mix.inputs["Fac"])
    nt.links.new(amb_bg.outputs["Background"], mix.inputs[1])
    nt.links.new(cam_bg.outputs["Background"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])


def setup_render(scene, args):
    r = scene.render
    r.resolution_x = r.resolution_y = args.res
    r.resolution_percentage = 100
    r.image_settings.file_format = "PNG"
    r.image_settings.color_depth = "8"
    r.image_settings.color_mode = "RGBA" if args.bg == "transparent" else "RGB"
    r.film_transparent = args.bg == "transparent"
    r.dither_intensity = 0.0
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.display_settings.display_device = "sRGB"
    if args.mode == "mask" or args.engine == "WORKBENCH":
        scene.render.engine = "BLENDER_WORKBENCH"
        sh = scene.display.shading
        sh.light = "FLAT"
        sh.color_type = "MATERIAL"
        sh.show_object_outline = False
        sh.show_specular_highlight = False
        sh.show_shadows = False
        sh.show_cavity = False
        try:
            scene.display.render_aa = "OFF" if args.mode == "mask" else "8"
        except Exception:
            pass
    elif args.engine == "EEVEE":
        scene.render.engine = "BLENDER_EEVEE"
    else:
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = args.samples
        scene.cycles.use_denoising = False
        scene.cycles.use_adaptive_sampling = False
        scene.cycles.max_bounces = 4


def place_camera(scene, pose, res):
    cam_data = bpy.data.cameras.new("CV_CAM_DATA")
    cam = bpy.data.objects.new("CV_CAM", cam_data)
    scene.collection.objects.link(cam)
    cam_data.sensor_fit = "AUTO"
    cam_data.sensor_width = cv_camera.SENSOR_MM
    cam_data.clip_start = pose["clip_start"]
    cam_data.clip_end = pose["clip_end"]
    if pose["type"] == "ORTHO":
        cam_data.type = "ORTHO"
        cam_data.ortho_scale = pose["ortho_scale"]
    else:
        cam_data.type = "PERSP"
        cam_data.lens = pose["focal_mm"]
    right, up, back = pose["rotation_matrix_columns"]
    loc = pose["location"]
    cam.matrix_world = Matrix((
        (right[0], up[0], back[0], loc[0]),
        (right[1], up[1], back[1], loc[1]),
        (right[2], up[2], back[2], loc[2]),
        (0.0, 0.0, 0.0, 1.0),
    ))
    scene.camera = cam
    return cam


def add_light(scene, pose):
    data = bpy.data.lights.new("CV_SUN", "SUN")
    data.energy = 1.6
    data.angle = math.radians(30)
    light = bpy.data.objects.new("CV_SUN", data)
    scene.collection.objects.link(light)
    right, up, back = pose["rotation_matrix_columns"]
    # Light travels along forward, tilted slightly down and to the right of the camera.
    d = Vector(pose["forward"]) + Vector(up) * -0.35 + Vector(right) * 0.25
    d.normalize()
    z = -d  # light looks down its local -Z
    x = Vector((0, 0, 1)).cross(z)
    if x.length < 1e-6:
        x = Vector((1, 0, 0))
    x.normalize()
    y = z.cross(x)
    light.matrix_world = Matrix(((x[0], y[0], z[0], 0), (x[1], y[1], z[1], 0),
                                 (x[2], y[2], z[2], 0), (0, 0, 0, 1)))


def mat_rows(m):
    return [[float(m[i][j]) for j in range(4)] for i in range(4)]


def main():
    args = parse_args()
    t0 = time.time()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    with open(args.spec, "r", encoding="utf8") as fh:
        spec = json.load(fh)
    include = [c for c in args.include.split(",") if c] or proxy_spec.POLICY_CLASSES[args.policy]
    parts = [p for p in spec["parts"] if p["class"] in include]
    flat = args.flat or args.mode == "mask"
    objs = []
    for p in parts:
        override = proxy_spec.CLASS_MASK_COLORS[p["class"]] if args.mode == "mask" else None
        if p["shape"] == "mesh":
            objs.extend(import_mesh_part(p, color_override=override, flat=flat))
        else:
            objs.append(build_part(p, color_override=override, flat=flat))
    bpy.context.view_layer.update()

    meta = {
        "render_call_id": args.render_call_id or str(uuid.uuid4()),
        "pid": os.getpid(),
        "mode": args.mode,
        "asset_id": spec.get("asset_id"),
        "model_revision": proxy_spec.spec_hash_stable(spec),
        "base_policy": args.policy,
        "included_classes": include,
        "included_objects": sorted(o.name for o in objs),
        "blender_version": bpy.app.version_string,
    }

    if args.mode == "glb":
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        tmp = args.out[:-4] + ".tmp.glb"   # exporter requires the .glb suffix
        bpy.ops.export_scene.gltf(filepath=tmp, export_format="GLB")
        os.replace(tmp, args.out)
        meta["output"] = os.path.basename(args.out)
        with open(args.meta, "w", encoding="utf8") as fh:
            json.dump(meta, fh, indent=2)
        return

    lo, hi = world_bounds(objs)
    persp = dict(quadrant=args.quadrant, elevation_deg=args.elevation, focal_mm=args.focal)
    pose = cv_camera.pose_for(args.view, lo, hi, **(persp if args.view == "perspective" else {}))
    setup_world(scene, args)
    setup_render(scene, args)
    cam = place_camera(scene, pose, args.res)
    if args.mode != "mask" and not flat:
        add_light(scene, pose)
    depsgraph = bpy.context.evaluated_depsgraph_get()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    tmp_png = args.out + ".tmp.png"
    scene.render.filepath = tmp_png
    bpy.ops.render.render(write_still=True)
    os.replace(tmp_png, args.out)

    fwd = cam.matrix_world.to_3x3() @ Vector((0, 0, -1))
    meta.update({
        "view": args.view,
        "output": os.path.basename(args.out),
        "engine": scene.render.engine,
        "resolution": [args.res, args.res],
        "background": args.bg,
        "camera": {
            "type": cam.data.type,
            "location": list(cam.location),
            "forward": [float(v) for v in fwd],
            "matrix_world": mat_rows(cam.matrix_world),
            "projection_matrix": mat_rows(cam.calc_matrix_camera(depsgraph, x=args.res, y=args.res)),
            "ortho_scale": cam.data.ortho_scale if cam.data.type == "ORTHO" else None,
            "lens_mm": cam.data.lens if cam.data.type == "PERSP" else None,
            "sensor_mm": cam.data.sensor_width,
            "target": list(pose["target"]),
            "quadrant": pose.get("quadrant"),
            "elevation_deg": pose.get("elevation_deg"),
        },
        "bounds": {"min": list(lo), "max": list(hi)},
        "seconds": round(time.time() - t0, 3),
    })
    with open(args.meta, "w", encoding="utf8") as fh:
        json.dump(meta, fh, indent=2)


main()
