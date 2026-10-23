"""Artifact analysis / repair algorithms on synthetic scenes with known ground truth."""
import numpy as np
import pytest

from cozyverse_worker import artifact_qa as aq


def scene(size=256, noise=0.0, cast=None, seed=1):
    """White backdrop, grey card (3 patches), blue box, orange lamp, corrugated rust panel."""
    rng = np.random.default_rng(seed)
    img = np.full((size, size, 3), 255, np.uint8)
    img[40:120, 30:230] = (150, 150, 150)                 # big neutral wall
    img[130:170, 30:110] = (90, 90, 92)
    img[130:170, 110:190] = (200, 200, 198)
    img[180:230, 30:100] = (40, 70, 160)                  # blue box (colourful, not neutral)
    img[180:230, 105:130] = (255, 150, 30)                # orange lamp (intentional warm, saturated)
    panel = np.zeros((50, 90, 3), np.uint8)
    stripe = (np.arange(90) // 3) % 2                      # corrugated: strong 3px stripes
    panel[:] = np.where(stripe[None, :, None] == 1, 150, 70).astype(np.uint8) * np.array([1.0, 0.8, 0.6])[None, None, :]
    img[180:230, 140:230] = panel.astype(np.uint8)
    fg = ~(img >= 253).all(axis=-1)
    lamp = np.zeros((size, size), bool); lamp[180:230, 105:130] = True
    flat = np.zeros((size, size), bool); flat[45:115, 35:225] = True
    stripes = np.zeros((size, size), bool); stripes[180:230, 140:230] = True
    if cast is not None:                                   # surface cast: lamps keep their own colour
        out = img.astype(np.float64)
        shift = fg & ~lamp
        out[shift] *= np.array(cast)
        img = np.clip(out, 0, 255).astype(np.uint8)
    if noise:
        n = rng.normal(0, noise, img.shape)
        img = np.clip(img.astype(np.float64) + np.where(fg[..., None], n, 0), 0, 255).astype(np.uint8)
    return img, {"lamp": lamp, "flat": flat, "stripes": stripes, "fg": fg}


YELLOW = (1.0, 1.0, 0.80)


def test_lab_roundtrip_is_accurate():
    rng = np.random.default_rng(0)
    rgb = rng.integers(0, 256, (500, 1, 3), dtype=np.uint8)
    back = aq.lab_to_srgb(aq.srgb_to_lab(rgb))
    assert np.abs(back.astype(int) - rgb.astype(int)).max() <= 1


def test_clean_neutral_scene_has_no_cast():
    img, m = scene()
    rep = aq.analyze(img)
    assert rep["metrics"]["cast"]["ok"] and rep["metrics"]["cast"]["magnitude"] < 2.0
    assert not [f for f in rep["findings"] if f["code"] == "COLOR_CAST"]


def test_yellow_cast_is_detected_with_direction_and_scene_grade_mode_leaves_it():
    img, _ = scene(cast=YELLOW)
    rep = aq.analyze(img)
    f = next(f for f in rep["findings"] if f["code"] == "COLOR_CAST")
    assert rep["metrics"]["cast"]["direction"] == "yellow" and rep["metrics"]["cast"]["magnitude"] >= aq.CAST_WARN
    assert f["severity"] in ("warning", "critical")
    grade = aq.analyze(img, aq.ArtifactSettings(mode="scene_grade"))
    assert [f["code"] for f in grade["findings"] if f["code"].startswith("CAST")] == ["CAST_SCENE_GRADE"]
    off = aq.analyze(img, aq.ArtifactSettings(neutralize_cast=False))
    assert "cast" not in off["metrics"]


def test_neutral_card_returns_to_neutral_and_warm_lamp_is_untouched():
    clean, m = scene()
    cast_img, _ = scene(cast=YELLOW)
    rep = aq.analyze(cast_img)
    c = rep["metrics"]["cast"]
    fixed, weight = aq.neutralize_cast(cast_img, (c["a"], c["b"]), m["fg"], strength=1.0)
    # neutral patches: colour difference to the clean reference shrinks to near zero
    labs = lambda x: aq.srgb_to_lab(x)
    patch = (slice(130, 170), slice(30, 190))
    before = aq.delta_e(labs(cast_img)[patch], labs(clean)[patch]).mean()
    after = aq.delta_e(labs(fixed)[patch], labs(clean)[patch]).mean()
    assert before > 6 and after < 0.35 * before and after < 5.0, (before, after)   # most of the cast is gone
    # the intentional warm lamp is byte-identical (protected), hue and chroma untouched
    lamp = m["lamp"]
    assert np.abs(fixed[lamp].astype(int) - cast_img[lamp].astype(int)).max() <= 1
    # the white backdrop stays pure white
    assert (fixed[~m["fg"]] == 255).all()
    # saturated blue box barely moves (weighted by chroma)
    blue = (slice(185, 225), slice(35, 95))
    assert aq.delta_e(labs(fixed)[blue], labs(cast_img)[blue]).mean() < 3.0


def test_correction_is_bounded_and_does_nothing_without_a_cast():
    clean, m = scene()
    out, _ = aq.neutralize_cast(clean, (0.0, 0.0), m["fg"])
    assert np.array_equal(out, clean)
    img, _ = scene(cast=(1.0, 1.0, 0.4))                      # extreme cast
    c = aq.analyze(img)["metrics"]["cast"]
    fixed, _ = aq.neutralize_cast(img, (c["a"], c["b"]), m["fg"], max_de=6.0)
    moved = aq.delta_e(aq.srgb_to_lab(fixed), aq.srgb_to_lab(img))[m["flat"]].mean()
    assert moved <= 6.5                                       # never exceeds the configured bound


def test_noise_measured_only_in_flat_areas_and_texture_is_not_noise():
    clean, m = scene()
    assert not [f for f in aq.analyze(clean)["findings"] if f["code"] == "NOISE"]
    noisy, _ = scene(noise=7.0)
    rep = aq.analyze(noisy)
    n = rep["metrics"]["noise"]
    assert n["ok"] and 1.0 <= n["sigma"] <= 6.0
    f = next(f for f in rep["findings"] if f["code"] == "NOISE")
    assert f["severity"] in ("warning", "critical")
    flat = rep["masks"]["flat"]
    assert not (flat & m["stripes"]).any()                    # corrugated rust is excluded from the noise estimate


def test_noise_reduction_spares_texture():
    noisy, m = scene(noise=7.0)
    rep = aq.analyze(noisy)
    fixed, w = aq.reduce_noise(noisy, rep["masks"]["flat"], 0.8)
    L = lambda x: aq.srgb_to_lab(x)[..., 0]
    inner = (slice(55, 105), slice(45, 215))
    assert L(fixed)[inner].std() < 0.5 * L(noisy)[inner].std()
    st = (slice(185, 225), slice(145, 225))
    contrast_before = L(noisy)[st].std()
    contrast_after = L(fixed)[st].std()
    assert contrast_after >= 0.95 * contrast_before           # stripes keep their contrast
    assert w[m["stripes"]].max() < 0.2


def test_waxy_skin_is_advisory_only_and_character_specific():
    rng = np.random.default_rng(3)
    img = np.full((200, 200, 3), 255, np.uint8)
    skin = (215, 160, 125)
    img[40:160, 50:150] = skin
    img[60:90, 70:110] = (250, 248, 245)                      # big specular highlight
    rep = aq.analyze(img, category="character")
    assert rep["metrics"]["waxy"]["suspect"] is True
    f = next(f for f in rep["findings"] if f["code"] == "WAXY_SKIN_SUSPECT")
    assert f["severity"] == "review"                          # human decision, never auto-fixed
    assert not hasattr(aq, "fix_waxy_skin")
    textured = img.copy()
    textured[40:160, 50:150] = np.clip(np.array(skin) + rng.normal(0, 9, (120, 100, 3)), 0, 255).astype(np.uint8)
    rep2 = aq.analyze(textured, category="character")
    assert rep2["metrics"]["waxy"]["suspect"] is False
    assert "waxy" not in aq.analyze(img, category="prop")["metrics"]          # skin heuristics are off for props
    assert "waxy" not in aq.analyze(img, aq.ArtifactSettings(avoid_waxy_skin=False), category="character")["metrics"]


def test_five_view_consistency():
    ok = {"front": [10, 10, 100, 200], "back": [12, 10, 98, 199], "left": [5, 11, 120, 201], "right": [9, 10, 99, 200]}
    assert aq.consistency_findings(ok, 256) == []
    bad = {**ok, "left": [5, 40, 120, 250]}
    f = aq.consistency_findings(bad, 256)
    assert f and f[0]["code"] == "INCONSISTENT_HEIGHT" and f[0]["severity"] == "critical"


def test_settings_validation_and_defaults_match_the_spec():
    s = aq.ArtifactSettings()
    assert all([s.neutralize_cast, s.preserve_warm_lights, s.reduce_noise, s.avoid_waxy_skin, s.preserve_texture,
                s.check_shape, s.check_text, s.check_background, s.check_consistency, s.show_diff]) and s.mode == "studio"
    with pytest.raises(ValueError):
        aq.ArtifactSettings.from_dict({"bogus": True})
    with pytest.raises(ValueError):
        aq.ArtifactSettings.from_dict({"reduce_noise": "yes"})
    with pytest.raises(ValueError):
        aq.ArtifactSettings.from_dict({"mode": "sepia"})


def test_diff_heatmap_marks_only_changed_pixels():
    a = np.full((20, 20, 3), 100, np.uint8)
    b = a.copy(); b[5:10, 5:10] += 30
    h = np.asarray(aq.diff_heatmap(a, b))
    assert h[7, 7].sum() > 0 and h[0, 0].sum() == 0


def test_lightness_dependent_curve_fixes_multiplicative_casts_better_than_one_shift():
    clean, m = scene()
    cast_img, _ = scene(cast=YELLOW)
    c = aq.analyze(cast_img)["metrics"]["cast"]
    assert len(c["curve"]) >= 2
    one, _ = aq.neutralize_cast(cast_img, (c["a"], c["b"]), m["fg"], max_de=30.0)           # bound lifted to isolate the curve
    two, _ = aq.neutralize_cast(cast_img, (c["a"], c["b"]), m["fg"], max_de=30.0, curve=c["curve"])
    patches = [(slice(130, 170), slice(30, 110)), (slice(130, 170), slice(110, 190)), (slice(40, 120), slice(30, 230))]
    def err(img):   # chroma error only: lightness changes from the cast are outside what a chroma correction touches
        d = aq.srgb_to_lab(img)[..., 1:] - aq.srgb_to_lab(clean)[..., 1:]
        return np.mean([np.hypot(d[p][..., 0], d[p][..., 1]).mean() for p in patches])
    assert err(two) < err(one) and err(two) < 1.5, (err(one), err(two))
