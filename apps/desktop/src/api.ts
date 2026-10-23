export type ViewName = "front" | "back" | "left" | "right" | "perspective";
export const VIEWS: ViewName[] = ["front", "back", "left", "right", "perspective"];
export type Policy = "none" | "building_foundation" | "local_plinth" | "scene_context" | "full_master_base";
export type ViewStatus = "BLOCKED" | "DRAFT" | "NEEDS_REVIEW" | "PASSED" | "FAILED";

export interface Finding { code: string; severity: "critical" | "warning"; detail: string }
export interface ViewInfo {
  status: ViewStatus;
  path: string | null;
  sha256: string | null;
  model_revision: string | null;
  camera_type: "ORTHO" | "PERSP";
  stale: boolean;
  qa: { findings?: Finding[]; analysis?: { width: number; height: number; margin: number } } | null;
  meta: {
    engine: string | null; resolution: [number, number] | null; base_policy: string | null;
    forward: number[] | null; ortho_scale: number | null; lens_mm: number | null;
    quadrant: string | null; elevation_deg: number | null; blender_version: string | null;
    seconds: number | null;
  } | null;
}
export interface Asset {
  id: string; project_id: string; code: string; name: string; category: string;
  mesh_status: string; model_revision: string | null; views: Record<ViewName, ViewInfo>;
}
export interface Project { id: string; name: string; slug: string }
export interface Job {
  id: string; kind: string; state: string; progress: number; message: string;
  result: { path?: string; folder?: string; status?: string; model_revision?: string } | null;
}
export interface BlenderInfo { found: boolean; path: string | null; version: string | null; device: string | null }
export interface RenderOptions {
  resolution: number; engine: "CYCLES" | "WORKBENCH"; samples: number;
  base_policies: Partial<Record<ViewName, Policy>>;
  perspective_quadrant: "front_left" | "front_right" | "rear_left" | "rear_right";
  perspective_elevation_degrees: number; perspective_focal_mm: number;
}
export interface Conn { base: string; token: string }

const isTauri = () => typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

/** Packaged app: the Tauri host starts the worker and hands over port + session token.
 *  Dev in a plain browser: VITE_WORKER_URL / VITE_WORKER_TOKEN (never put these in a URL). */
export async function getConnection(): Promise<Conn> {
  if (isTauri()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<Conn>("worker_connection");
  }
  const base = import.meta.env.VITE_WORKER_URL as string | undefined;
  const token = import.meta.env.VITE_WORKER_TOKEN as string | undefined;
  if (!base || !token) throw new Error("Worker connection not configured (set VITE_WORKER_URL and VITE_WORKER_TOKEN).");
  return { base, token };
}

export async function openFolder(path: string): Promise<boolean> {
  if (!isTauri()) return false;
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("open_folder", { path });
  return true;
}
export const canOpenFolder = isTauri;

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export function client(conn: Conn) {
  async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await fetch(conn.base + path, {
      method,
      headers: { Authorization: `Bearer ${conn.token}`, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : "Request rejected"; } catch { /* keep */ }
      throw new ApiError(res.status, detail);
    }
    return (await res.json()) as T;
  }
  return {
    health: () => req<{ status: string; blender: BlenderInfo }>("GET", "/health"),
    projects: () => req<Project[]>("GET", "/projects"),
    createProject: (name: string, fixtures: string[]) => req<Project>("POST", "/projects", { name, fixtures }),
    assets: (pid: string) => req<Asset[]>("GET", `/projects/${pid}/assets`),
    buildProxy: (aid: string, params?: { plinth_margin: number; plinth_height: number }) =>
      req<{ job_id: string }>("POST", `/assets/${aid}/build-proxy`, params),
    renderFive: (aid: string, o: RenderOptions) => req<{ job_id: string }>("POST", `/assets/${aid}/render-five`, {
      resolution: o.resolution, engine: o.engine, samples: o.samples, background: "white",
      base_policies: o.base_policies, perspective_quadrant: o.perspective_quadrant,
      perspective_elevation_degrees: o.perspective_elevation_degrees, perspective_focal_mm: o.perspective_focal_mm,
    }),
    review: (aid: string, view: ViewName, status: "PASSED" | "FAILED" | "NEEDS_REVIEW") =>
      req<Asset>("POST", `/assets/${aid}/views/${view}/review`, { status }),
    job: (jid: string) => req<Job>("GET", `/jobs/${jid}`),
    cancel: (jid: string) => req<Job>("DELETE", `/jobs/${jid}`),
    exportProject: (pid: string) => req<{ job_id: string }>("POST", `/projects/${pid}/export`),
    setBlender: (path: string) => req<BlenderInfo>("PUT", "/settings/blender", { path }),
    blender: () => req<BlenderInfo>("GET", "/settings/blender"),
    async imageUrl(aid: string, view: ViewName): Promise<string> {
      const res = await fetch(`${conn.base}/assets/${aid}/views/${view}/image`, {
        headers: { Authorization: `Bearer ${conn.token}` },
      });
      if (!res.ok) throw new ApiError(res.status, "image unavailable");
      return URL.createObjectURL(await res.blob());
    },
  };
}
export type Api = ReturnType<typeof client>;
