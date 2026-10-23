# Acceptance status against the kit's checklists

Legend: **Verified** means an automated test or a driven run proves it. **Partial** means some of it works. **Not done** is stated plainly. Tests are in `tests/`.

## ACCEPTANCE_CHECKLIST.md

### Five view strictness
| Item | Status | Evidence |
|---|---|---|
| Front/back/left/right are ORTHO with the right forward vectors | Verified | `test_camera_matrices_read_from_blender` (matrices read back from Blender) |
| Zero inclination and roll, no adjacent walls | Verified | `test_each_elevation_shows_only_its_own_face` (per-face colour leakage), camera-math tests |
| Perspective is PERSP and shows several faces | Verified | `test_perspective_shows_two_faces`, `..._differs_from_every_elevation` |
| Five separate Blender PNGs with path, camera JSON, dimensions and checksum | Verified | `test_five_separate_invocations`, golden path export |
| Changing the model invalidates stale statuses and views | Verified | `test_golden_path` (stale), per-view and full re-render |
| A failure after some views never yields a PASSED set | Verified | `test_failure_after_three_views_never_yields_a_passed_set` |
| 2D concept sheets never count as individual outputs | Verified | `test_contact_sheet_is_supplementary_and_never_replaces_the_five_files` |

### Model geometry and QA
| Item | Status | Evidence |
|---|---|---|
| Asymmetric fixture makes every side unmistakable | Verified | `test_asymmetric_peg_side_matches_convention` |
| Underside, tank and awning in frame, 8% margin, true white | Verified | `test_production_cycles_render_is_pure_white_and_uncropped`, margin checks |
| No mirrored lettering | Not applicable | Proxies contain no lettering. Sign text is a manual 2D overlay. |
| Inferred rear wall flagged and reviewable | Verified | `inferred_regions` in manifests, review notice in the UI |
| Source colour warmth stays local | Verified | `test_neutral_card_returns_to_neutral_and_warm_lamp_is_untouched` |

### Inventory and UX
| Item | Status | Evidence |
|---|---|---|
| Add tiny props, distinguish instances from unique assets | Verified | `test_hierarchy_ids_merge_history_and_tiny_props` |
| Crop and mask show what was seen; unseen noted separately | Verified | `test_crop_and_polygon_mask_come_from_the_source`, inferred features |
| Manual, proxy, render and export work without API keys | Verified | every Blender test runs keyless |
| Jobs cancel and recover cleanly | Verified | cancel test checks no orphaned Blender; restart marks interrupted jobs |
| Failed exports preserve approved output | Verified | export writes to a temp file then renames |
| Each view has preview, file, save info and re-render | Partial | Preview, file name, Save PNG and per-view re-render exist. No "open file in Explorer" control. |
| Attractive UI, buttons not pills, images fill cards | Verified | reviewed in the browser; 4 to 8 px radii, `object-fit: contain` on white frames |

### Export and security
| Item | Status | Evidence |
|---|---|---|
| ZIP read-back, schema-valid JSON, sanitised paths, SHA-256 | Verified | `_verify_zip`, schema validation in `test_golden_path` |
| Keys absent from exports and logs; network use opt-in; costs approved | Verified | `test_keys_never_returned_and_stored_only_in_vault`, consent tests |
| Installer smoke-tested on a clean Windows 11 machine | **Not done** | Only tested on the development machine (see README) |

## ADDITIONAL_ACCEPTANCE_TESTS.md (v1.1)
All of these are **Verified** except where noted: strict elevations; `perspective.png` truly perspective; separate engine invocations; `building_foundation` excludes the master slab; `local_plinth` only where requested; `BASE-001` editable with five views plus top; master slab for beauty renders but not building shots; camera fits the active policy; neutral card stays neutral with warm lamps intact; noise reduction spares corrugated texture; no blanket skin smoothing (advisory only, no auto fix); material-first remediation with before/after/diff kept (the re-render recommendation is text, the re-render itself is a manual click); export blocks critical findings or records a written override.

## DUAL_MODE_PROVIDER_STYLE_ACCEPTANCE.md (v1.2)
| Item | Status | Evidence |
|---|---|---|
| Home shows Create Diorama, Create Regular Image and Import equally | Verified | driven in the UI |
| Generate in both modes (mock in CI, live opt-in) | Partial | Both modes verified with the built-in generator. **Live adapters were never run**, no keys. |
| Import and decompose photos, paintings, line art, characters, miniatures; no forced base | Partial | Real photo and generated scenes verified; other kinds use the same path but were not each tested |
| 2D, 2.5D, 3D styles and saved recipes in both modes | Verified | catalogue and recipe tests |
| Mode switch keeps revisions, masks and recipes | Verified | `test_mode_switch_keeps_sources_and_items`. The prompt about incompatible outputs is a notice, not a blocking dialog. |
| Google, OpenAI, Kie mock-tested; live capability-checked and entitlement-gated | Partial | mock transports and listing health checks; live not run |
| No silent model substitution; failures and estimates shown | Verified | `test_google_request_shape_errors_and_no_silent_fallback` |
| Source approval records hash, prompt, model, provider, resolution, style, edits | Verified | `test_mock_generation_is_free_deterministic_and_records_provenance` |
| Inventory with tiny props, counts, source links | Verified | inventory tests |
| Diorama has a configurable master base; regular images start without one and can opt in | Verified | `test_regular_image_renders_without_any_base`, base-mode control |
| Four orthographic cameras and a separate perspective render | Verified | see above |
| 2D extrapolations carry UNVERIFIED_2D | Verified | `test_regular_image_two_d_export_is_complete_without_any_3d` (no angle images are invented) |
| Artifact preview keeps glow, gold, clay/gloss and grain | Partial | warm lamps and neon are protected; style-specific allowances (grain, clay gloss) are not separately modelled |
| Export manifest schema-valid with real hashes | Verified | export tests |

## Not built
OCR of sign text, shape-versus-source comparison, halo and jaggies detection, GPU selection, code signing, auto-update, clean-machine installer test, live provider verification, Tripo.
