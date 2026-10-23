"""Art-style engine: declarative recipes (not provider-bound), user presets, blending and prompt compilation.

Styles are described by formal traits (medium, line, palette, texture, lighting). No artist names.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from .config import SCHEMA_DIR
from .db import Database, dumps, now

DIMENSIONS = ("2D", "2.5D", "3D", "photo")
RECIPE_FIELDS = ("medium", "dimensionality", "palette", "lighting", "linework", "texture", "camera",
                 "negative_constraints", "strength")

# id -> (dimensionality, traits)
TRAITS: dict[str, tuple[str, str]] = {
    # 2D illustration and paint
    "anime_cel": ("2D", "flat cel-shaded colour areas, clean uniform outlines, hard two-tone shadows, bright saturated palette"),
    "manga_ink": ("2D", "black ink linework, screentone dots, high-contrast monochrome panels, expressive hatching"),
    "graphic_novel": ("2D", "bold inked shapes, heavy blacks, limited muted palette, dramatic panel-like composition"),
    "storybook": ("2D", "soft hand-painted children's picture-book look, gentle rounded shapes, warm friendly colours"),
    "paper_cutout": ("2.5D", "layered cut-paper shapes with slight drop shadows, visible paper fibre, flat colours"),
    "pixel_art": ("2D", "low-resolution pixel grid, limited colour palette, crisp aliased edges, no gradients"),
    "watercolor": ("2D", "transparent watercolour washes, soft bleeding edges, visible paper grain, light pigments"),
    "gouache": ("2D", "opaque matte gouache paint, flat layered colour, subtle brush marks, rich saturated palette"),
    "oil_painting": ("2D", "thick oil paint, visible brush strokes, blended colour transitions, canvas texture"),
    "risograph": ("2D", "two or three spot-ink colours, grainy halftone overprint, slight misregistration"),
    "linocut": ("2D", "carved relief print, bold black-and-one-colour shapes, rough cut edges"),
    "vector_flat": ("2D", "flat vector shapes, no outlines or thin geometric strokes, solid colours, minimal detail"),
    "line_art": ("2D", "clean monoline drawing on plain background, no shading, consistent stroke width"),
    "comic_ligne_claire": ("2D", "clear uniform outline, flat colours, precise architectural detail, no hatching"),
    "pencil": ("2D", "graphite pencil drawing, visible hatching, soft smudged shading, paper texture"),
    "charcoal": ("2D", "smudged charcoal strokes, deep soft blacks, rough paper tooth, dramatic tonal range"),
    "ink_wash": ("2D", "brush-and-ink wash, fluid tonal gradients, expressive brush strokes, quiet negative space"),
    "art_nouveau": ("2D", "flowing organic curves, decorative borders, muted earthy palette, ornamental linework"),
    "art_deco": ("2D", "symmetrical geometric ornament, gold and deep-colour palette, streamlined stylised forms"),
    "stained_glass": ("2D", "bold dark leading lines between jewel-toned translucent glass panes"),
    "mosaic": ("2D", "small tesserae tiles with visible grout lines, limited tile palette"),
    "blueprint": ("2D", "white technical linework on deep blue, dimension marks, orthographic drafting layout"),
    "silhouette": ("2D", "solid dark silhouettes against a bright graded backdrop, no interior detail"),
    "bw_comic": ("2D", "black-and-white comic inking, spotted blacks, crosshatch shading, no colour"),
    # tactile / handmade 3D
    "claymation": ("3D", "hand-sculpted modelling clay, fingerprint texture, soft rounded forms, stop-motion look"),
    "wool_knit": ("3D", "knitted wool yarn texture, visible stitches, soft fuzzy edges, cozy handmade feel"),
    "felt_puppet": ("3D", "stitched felt fabric, fuzzy surface, simple button details, handmade puppet look"),
    "papercraft": ("3D", "folded paper construction, crisp creases, matte card colours, soft shadows"),
    "cardboard_diorama": ("3D", "corrugated cardboard layers, tape and glue marks, handmade model-making look"),
    "wooden_toy": ("3D", "painted wooden toy blocks, rounded edges, visible wood grain, chunky simple shapes"),
    "vinyl_collectible": ("3D", "glossy vinyl designer-toy finish, smooth seams, bold simple colour blocks"),
    "resin_miniature": ("3D", "hand-painted resin miniature, tiny precise detail, dry-brushed edges, glossy base"),
    "stop_motion": ("3D", "stop-motion puppet set, tactile handmade props, practical lighting, shallow depth of field"),
    "plush_toy": ("3D", "soft plush fabric, stuffed rounded forms, visible seams, cuddly proportions"),
    "dollhouse_cutaway": ("3D", "dollhouse cutaway rooms, open back wall, tiny furnished interiors, warm lights"),
    # CG
    "stylized_cgi": ("3D", "stylised 3D animation render, exaggerated friendly proportions, soft global illumination"),
    "photoreal_cgi": ("3D", "physically based photoreal 3D render, accurate materials, realistic lighting and reflections"),
    "low_poly": ("3D", "low-polygon faceted geometry, flat-shaded triangles, simple solid colours"),
    "voxel": ("3D", "cube voxel construction, blocky shapes, uniform grid, bright simple colours"),
    "cel_shaded_3d": ("3D", "3D with cel shading, ink outlines on edges, two-step lighting, vivid colours"),
    "isometric_3d": ("3D", "isometric 3D view, clean geometry, soft shadows, tidy miniature-city look"),
    "hand_painted_game": ("3D", "hand-painted game-asset textures, stylised painterly shading, no photographic detail"),
    # photography and cinematic
    "documentary": ("photo", "natural documentary photograph, available light, candid framing, realistic colour"),
    "cinematic": ("photo", "cinematic still, motivated lighting, shallow depth of field, graded colour, film composition"),
    "product": ("photo", "studio product photograph, seamless backdrop, soft even light, crisp detail"),
    "macro": ("photo", "macro photograph, extreme close-up, razor-thin focus plane, fine surface detail"),
    "miniature_tilt_shift": ("photo", "tilt-shift photograph making the scene look like a tiny model, selective blur, saturated colour"),
    "golden_hour": ("photo", "low warm sun, long soft shadows, gentle golden rim light, natural colour balance"),
    "low_key": ("photo", "low-key chiaroscuro, deep shadows, single directional light, rich contrast"),
    "cyberpunk": ("3D", "rain-slicked neon city, magenta and cyan glow, dense signage, reflective wet surfaces"),
    "solarpunk": ("2.5D", "lush greenery integrated with clean architecture, bright optimistic daylight, solar panels"),
    "afrofuturism": ("2.5D", "bold geometric patterns, rich gold and deep jewel colours, futuristic forms rooted in African design"),
    "concept_art": ("2.5D", "painterly concept art, strong silhouette and value structure, atmospheric depth"),
}
EXTRA_LABELS = {
    "pencil": "Pencil", "charcoal": "Charcoal", "ink_wash": "Ink Wash", "art_nouveau": "Art Nouveau",
    "art_deco": "Art Deco", "stained_glass": "Stained Glass", "mosaic": "Mosaic", "blueprint": "Blueprint",
    "silhouette": "Silhouette", "bw_comic": "Black and White Comic", "stop_motion": "Stop-Motion Miniature",
    "plush_toy": "Plush Toy", "dollhouse_cutaway": "Dollhouse Cutaway", "golden_hour": "Golden Hour",
    "low_key": "Low-Key Chiaroscuro", "cyberpunk": "Cyberpunk", "solarpunk": "Solarpunk",
    "afrofuturism": "Afrofuturism", "concept_art": "Concept Art",
}
EXTRA_MEDIUM = {
    "pencil": "illustration_2d", "charcoal": "illustration_2d", "ink_wash": "paint_2d", "art_nouveau": "illustration_2d",
    "art_deco": "illustration_2d", "stained_glass": "illustration_2d", "mosaic": "illustration_2d",
    "blueprint": "illustration_2d", "silhouette": "illustration_2d", "bw_comic": "illustration_2d",
    "stop_motion": "tactile_3d", "plush_toy": "tactile_3d", "dollhouse_cutaway": "tactile_3d",
    "golden_hour": "photography", "low_key": "photography", "cyberpunk": "cg_3d", "solarpunk": "hybrid",
    "afrofuturism": "hybrid", "concept_art": "hybrid",
}
# default look keeps scene modes independent of style
MODE_OVERLAYS = {
    "DIORAMA": "presented as a miniature diorama scene on a raised display base, tabletop scale, handcrafted detail",
    "REGULAR_IMAGE": "",
}
STRENGTH_WORDS = [(0.34, "with a subtle influence of"), (0.67, "clearly in the style of"), (1.01, "strongly and consistently in the style of")]


def catalog() -> list[dict]:
    seed = json.loads((SCHEMA_DIR / "style_catalog.seed.json").read_text(encoding="utf8"))
    out = []
    seen = set()
    for p in seed["starter_presets"]:
        dim, traits = TRAITS[p["id"]]
        out.append({"id": p["id"], "label": p["label"], "medium": p["medium"], "dimensionality": dim,
                    "traits": traits, "owner": "app", "version": 1})
        seen.add(p["id"])
    for sid, (dim, traits) in TRAITS.items():
        if sid not in seen:
            out.append({"id": sid, "label": EXTRA_LABELS.get(sid, sid.replace("_", " ").title()),
                        "medium": EXTRA_MEDIUM.get(sid, "hybrid"), "dimensionality": dim, "traits": traits,
                        "owner": "app", "version": 1})
    return out


@dataclass
class StyleCompileResult:
    prompt: str
    negative_prompt: str
    warnings: list[str] = field(default_factory=list)
    truncated_fields: list[str] = field(default_factory=list)
    effective: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"prompt": self.prompt, "negative_prompt": self.negative_prompt, "warnings": self.warnings,
                "truncated_fields": self.truncated_fields, "effective": self.effective}


class StyleError(ValueError):
    pass


class StyleStore:
    def __init__(self, db: Database):
        self.db = db
        self._catalog = {c["id"]: c for c in catalog()}

    # ----- user recipes -----
    def validate_recipe(self, r: dict) -> dict:
        if not isinstance(r, dict):
            raise StyleError("recipe must be an object")
        name = str(r.get("name", "")).strip()
        if not 1 <= len(name) <= 80:
            raise StyleError("name is required (max 80 characters)")
        dim = r.get("dimensionality", "2D")
        if dim not in DIMENSIONS:
            raise StyleError("dimensionality must be 2D, 2.5D, 3D or photo")
        strength = r.get("strength", 0.7)
        if not isinstance(strength, (int, float)) or not 0 <= strength <= 1:
            raise StyleError("strength must be between 0 and 1")
        blend = r.get("blend") or []
        if not isinstance(blend, list) or len(blend) > 4:
            raise StyleError("blend accepts up to 4 styles")
        clean_blend = []
        for b in blend:
            if not isinstance(b, dict) or b.get("style_id") not in self._catalog and not self.get_user(str(b.get("style_id"))):
                raise StyleError("blend references an unknown style")
            w = b.get("weight", 1.0)
            if not isinstance(w, (int, float)) or not 0 < w <= 1:
                raise StyleError("blend weights must be between 0 and 1")
            clean_blend.append({"style_id": b["style_id"], "weight": float(w)})
        out: dict[str, Any] = {"name": name, "medium": str(r.get("medium", ""))[:60], "dimensionality": dim,
                               "strength": float(strength), "blend": clean_blend}
        for k in ("palette", "lighting", "linework", "texture", "camera"):
            v = r.get(k, "")
            if not isinstance(v, str) or len(v) > 400:
                raise StyleError(f"{k} must be text up to 400 characters")
            out[k] = v.strip()
        neg = r.get("negative_constraints", [])
        if not (isinstance(neg, list) and len(neg) <= 20 and all(isinstance(n, str) and len(n) <= 80 for n in neg)):
            raise StyleError("negative constraints: up to 20 short phrases")
        out["negative_constraints"] = [n.strip() for n in neg if n.strip()]
        refs = r.get("reference_ids", [])
        if not (isinstance(refs, list) and len(refs) <= 8 and all(isinstance(x, str) and len(x) <= 64 for x in refs)):
            raise StyleError("reference_ids: up to 8 source ids")
        out["reference_ids"] = refs
        return out

    def create(self, recipe: dict) -> dict:
        clean = self.validate_recipe(recipe)
        rid = "u_" + uuid.uuid4().hex[:10]
        self.db.execute("INSERT INTO style_recipes(id,name,recipe_json,version,favorite,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?)", (rid, clean["name"], dumps(clean), 1, 0, now(), now()))
        return self.get_user(rid)  # type: ignore[return-value]

    def update(self, rid: str, recipe: dict) -> dict:
        row = self.db.one("SELECT * FROM style_recipes WHERE id=? AND deleted=0", (rid,))
        if not row:
            raise LookupError("style")
        clean = self.validate_recipe(recipe)
        self.db.execute("INSERT INTO style_recipe_history(recipe_id,version,recipe_json,at) VALUES(?,?,?,?)",
                        (rid, row["version"], row["recipe_json"], now()))
        self.db.execute("UPDATE style_recipes SET name=?, recipe_json=?, version=version+1, updated_at=? WHERE id=?",
                        (clean["name"], dumps(clean), now(), rid))
        return self.get_user(rid)  # type: ignore[return-value]

    def delete(self, rid: str) -> None:
        if not self.db.one("SELECT id FROM style_recipes WHERE id=? AND deleted=0", (rid,)):
            raise LookupError("style")
        self.db.execute("UPDATE style_recipes SET deleted=1 WHERE id=?", (rid,))   # soft delete keeps provenance valid

    def favorite(self, style_id: str, on: bool) -> None:
        if style_id in self._catalog:
            self.db.execute("INSERT INTO style_favorites(style_id) VALUES(?) ON CONFLICT DO NOTHING", (style_id,)) if on else \
                self.db.execute("DELETE FROM style_favorites WHERE style_id=?", (style_id,))
        elif self.get_user(style_id):
            self.db.execute("UPDATE style_recipes SET favorite=? WHERE id=?", (1 if on else 0, style_id))
        else:
            raise LookupError("style")

    def get_user(self, rid: str) -> Optional[dict]:
        row = self.db.one("SELECT * FROM style_recipes WHERE id=? AND deleted=0", (rid,))
        if not row:
            return None
        r = json.loads(row["recipe_json"])
        return {"id": row["id"], "label": row["name"], "owner": "user", "version": row["version"],
                "favorite": bool(row["favorite"]), **r}

    def list(self) -> list[dict]:
        favs = {r["style_id"] for r in self.db.all("SELECT style_id FROM style_favorites")}
        out = [{**c, "favorite": c["id"] in favs} for c in self._catalog.values()]
        for row in self.db.all("SELECT id FROM style_recipes WHERE deleted=0 ORDER BY updated_at DESC"):
            out.append(self.get_user(row["id"]))
        return out

    def resolve(self, style_id: str) -> dict:
        if style_id in self._catalog:
            return dict(self._catalog[style_id])
        u = self.get_user(style_id)
        if not u:
            raise StyleError("unknown style")
        return u

    def export_recipes(self) -> dict:
        items = [self.get_user(r["id"]) for r in self.db.all("SELECT id FROM style_recipes WHERE deleted=0")]
        return {"format": "cozyverse-styles", "version": 1,
                "recipes": [{k: v for k, v in i.items() if k not in ("id", "owner", "version", "favorite", "label")} for i in items if i]}

    def import_recipes(self, doc: dict) -> list[dict]:
        if not (isinstance(doc, dict) and doc.get("format") == "cozyverse-styles" and isinstance(doc.get("recipes"), list)
                and len(doc["recipes"]) <= 100):
            raise StyleError("not a CozyVerse style file")
        return [self.create(r) for r in doc["recipes"]]

    # ----- compilation -----
    def snapshot(self, style_id: str) -> dict:
        """Immutable copy recorded in source lineage so later edits of a recipe never rewrite history."""
        s = self.resolve(style_id)
        return {"id": style_id, "version": s.get("version", 1), "label": s.get("label"), "recipe": s}

    def compile(self, style_id: Optional[str], user_prompt: str, negative: str = "", scene_mode: str = "REGULAR_IMAGE",
                caps: Optional[dict] = None, strength: Optional[float] = None,
                recipe: Optional[dict] = None) -> StyleCompileResult:
        caps = caps or {}
        warnings: list[str] = []
        truncated: list[str] = []
        user_prompt = user_prompt.strip()
        if not user_prompt:
            raise StyleError("prompt is required")
        parts: list[str] = []
        negs: list[str] = [n.strip() for n in negative.split(",") if n.strip()]
        eff_strength = None
        if recipe is not None:                       # an unsaved recipe being previewed in the editor
            clean = self.validate_recipe(recipe)
            s = {**clean, "label": clean["name"], "traits": ""}
            style_id = "(unsaved)"
        elif style_id:
            s = self.resolve(style_id)
        if style_id:
            eff_strength = strength if strength is not None else s.get("strength", 0.7)
            word = next(w for lim, w in STRENGTH_WORDS if eff_strength < lim)
            traits = [s["traits"]] if s.get("traits") else []
            for b in sorted(s.get("blend", []), key=lambda b: -b["weight"]):
                bs = self.resolve(b["style_id"])
                traits.append(f"blended with {bs.get('label')} ({bs.get('traits', '')})")
            for k, lbl in (("palette", "palette"), ("lighting", "lighting"), ("linework", "linework"),
                           ("texture", "texture"), ("camera", "camera")):
                if s.get(k):
                    traits.append(f"{lbl}: {s[k]}")
            parts.append(f"Rendered {word} {s.get('label')}: " + "; ".join(traits) + ".")
            negs += s.get("negative_constraints", [])
        overlay = MODE_OVERLAYS.get(scene_mode, "")
        if overlay:
            parts.append(overlay[0].upper() + overlay[1:] + ".")
        style_text = " ".join(parts)
        limit = caps.get("max_prompt_chars")
        if limit and len(user_prompt) + 1 + len(style_text) > limit:
            room = max(0, limit - len(user_prompt) - 1)
            style_text = style_text[:room].rsplit(" ", 1)[0] if room else ""
            truncated.append("style")
            warnings.append(f"Style text was shortened to fit the model's {limit}-character prompt limit. Your own wording is untouched.")
        final = (user_prompt + " " + style_text).strip()
        neg_text = ", ".join(dict.fromkeys(negs))
        if neg_text and not caps.get("negative_prompt", False):
            final = (final + f" Avoid: {neg_text}.").strip()
            warnings.append("This model has no negative-prompt field, so avoid-terms were added to the prompt as best-effort text.")
            neg_text_out = ""
        else:
            neg_text_out = neg_text
        return StyleCompileResult(prompt=final, negative_prompt=neg_text_out, warnings=warnings,
                                  truncated_fields=truncated,
                                  effective={"style_id": style_id, "strength": eff_strength, "scene_mode": scene_mode})
