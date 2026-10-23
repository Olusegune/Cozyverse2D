# Phases 1 to 3: security design and review (rafter-secure-design + rafter-code-review walk)

New surface since Phase 0: image and GLB uploads, outbound provider HTTP, API keys, result-URL downloads, AI-generated text and files, a larger local API, and the installed app.

## Trust boundaries
```
[UI] -(1)-> [Worker 127.0.0.1] -(2)-> [Blender]            (1) bearer token  (2) argv list + JSON spec file
                 |-(3)-> [SQLite / project dir]            (3) server-built paths only
                 |-(4)-> [Provider HTTPS APIs]             (4) allow-listed hosts, key from vault, user approval
                 |-(5)-> [Result URL download]             (5) SSRF guard
[User files] -(6)-> upload endpoints                       (6) decode-validated, size capped
```

## Decisions and where they are tested
| Risk | Control | Test |
|---|---|---|
| Hostile image upload (polyglot, bomb, SVG, truncated) | Decode with Pillow, format allow-list, 60 MP and 40 MB caps, animated rejected, server-chosen names, read-only originals, thumbnails re-encoded | `test_sources_inventory`: 8 hostile cases, 413 at the edge, client filename never used |
| Hostile GLB | Pure-Python header/JSON validation: external URIs and unknown required extensions rejected, size and node limits; Blender importer runs only on validated files | `test_mesh_and_proxies`, `test_ai_pipeline` hostile downloads |
| Provider keys | OS credential store only; never returned by the API, never in files or job messages; key set rejects whitespace and control characters | `test_keys_never_returned_and_stored_only_in_vault` scans every file under the data dir |
| Outbound requests | Fixed provider base URLs; capability gate runs before any request; no silent fallback (a 401 sends exactly one request) | `test_capability_gating...`, `test_google_request_shape_errors_and_no_silent_fallback` |
| Cost and consent | Estimate then one-shot approval token; request hash must match; expiry; daily/monthly caps; local-only mode; create calls never auto-retried | `test_paid_flow...`, `test_estimate_expiry_and_budget_caps...`, `test_meshy_create_is_never_retried...` |
| Data leaving the device | Estimate lists prompt size, each reference with SHA-256, re-encoded PNG without EXIF | `test_paid_flow...` checks the sent image has no EXIF |
| SSRF via provider result URLs | https only, no IP literals or userinfo, every resolved address must be public (v4, v6, v4-mapped), connection pinned to the checked IP, no redirects, size and content-type caps | 20+ cases in `test_styles_providers`, plus Kie and Meshy hostile URLs |
| Prompt injection via AI output | Model text is parsed as data (JSON list), each item validated and clamped; nothing is executed or acted on; proposals start `unconfirmed` | `test_google_vision_refuses_hostile_or_malformed_model_output` |
| Mass assignment and parameter abuse | Pydantic `extra="forbid"`, strict booleans for settings, bounded numbers, regex IDs | validation tests in every suite |
| Path traversal | IDs are regexes, paths are built server-side, `safe_join` after resolve; ZIP names checked on write and read | Phase 0 tests plus export tests |
| CORS | Only the app origins; Vite origins only when `CV_DEV_ORIGINS=1` | `test_cors_allows_only_the_app_origins_by_default` |
| Resource exhaustion | Per-route body limits (1 MB default, 40 MB images, 100 MB meshes), one job at a time, queue cap, timeouts | body-limit tests |

## Residual risks (accepted, written down)
1. Anything running as the same Windows user can read the worker's session token or the user's credential-store entries. This is the normal desktop trust model.
2. Blender detection still probes `blender.exe` found on `PATH`, `CV_BLENDER` or the saved path with `--version`.
3. Provider output (images and meshes) is validated structurally, not scanned for malware.
4. The `.cvbd` file is a pointer with no secrets, but opening one reveals whether a project id exists on this machine.
5. Live provider calls are not verified with real keys; contract tests use recorded request shapes.
6. `rafter run` (remote SAST/SCA) needs a GitHub token for the private repo and has not run yet.

## Local checks run
`rafter secrets .` found only third-party test vectors inside `.venv` (numpy). See `phase0-review.md`.
