# ADR 0002: Installer, bundled Python runtime, Blender not bundled

Status: accepted, 2026-10-23

- **Installer:** Tauri 2 NSIS, per-user (`installMode: currentUser`), no administrator rights. Built by `scripts/build_installer.ps1`.
- **Python worker:** the installed app must not depend on a user Python. `scripts/build_runtime.ps1` copies the interpreter the repo `.venv` was made from plus the venv packages (minus pytest/pip) into `runtime/python`, which is bundled at `resources/worker/python`. Nothing is downloaded. Cost: about 106 MB, mostly numpy.
- **Resource layout:** `resources/worker/cozyverse_worker`, `resources/blender`, `resources/schemas`, so `config.py` finds `BLENDER_DIR` and `SCHEMA_DIR` with the same relative paths as in the repo.
- **Blender is not bundled.** It is GPL, very large, and users often already have it. The app detects it at startup, shows a visible chip, and lets the user set the path in Settings. Rendering is disabled with a clear message when it is missing.
- **Project files:** `.cvbd` is registered per user by an NSIS hook (`installer/hooks.nsh`) with the supplied file icon. A `.cvbd` is a tiny pointer to a project on that computer, not a package; sharing uses Export ZIP.
- **Dev origins:** the Vite origins are allowed in CORS only when `CV_DEV_ORIGINS=1`, which only the dev script and debug builds set.
- **Not done:** code signing, auto-update channel, SBOM, and a smoke test on a clean Windows 11 machine.
