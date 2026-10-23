# ADR 0001: Blender version, repo location and stack

Status: accepted, 2026-10-23

- Blender 4.4 is the version named in the kit, but its install folder on this machine is empty. Installed and runnable: 5.1.1 and 5.2.2 LTS. Decision: target **Blender 5.2 LTS**, keep the renderer script free of version-specific API where possible, and record the detected version in every render's metadata.
- `Olusegune/Cozyverse2D` is empty and `gh` is not logged in, so work is local in `C:\Users\eduni\Cozyverse2D` with `origin` set. Nothing is pushed until the user logs in and asks.
- The old repo (`Cozyverse`, branch `decompose-golden-path`) is a different product (Cozyverse Studio, image/world creation). Only its Tauri/Vite/React/Tailwind conventions are reused as a reference; no code is copied.
- Stack per kit: Tauri 2, React/TypeScript/Vite, Python 3.11 FastAPI worker, SQLite, Blender Python adapter.
- Elevation cameras are placed with an explicit rotation matrix (no look-at/track-to) so roll and inclination are exactly zero.
