export type ViewName = "front" | "back" | "left" | "right" | "perspective";
export const VIEWS: ViewName[] = ["front", "back", "left", "right", "perspective"];
export type Policy = "none" | "building_foundation" | "local_plinth" | "scene_context" | "full_master_base";
export type ViewStatus = "BLOCKED" | "DRAFT" | "NEEDS_REVIEW" | "PASSED" | "FAILED";
export type SceneMode = "DIORAMA" | "REGULAR_IMAGE";
export type OutputGoal = "2D_LAYER_EXPORT" | "REFERENCE_ONLY" | "3D_PROXY" | "3D_RECONSTRUCTION" | "SCENE_REASSEMBLY";

export interface Finding { code: string; severity: "critical" | "warning" | "review" | "info"; detail: string; score?: number; remediation?: string[] }
export interface ViewInfo {
  status: ViewStatus; path: string | null; sha256: string | null; model_revision: string | null;
  camera_type: "ORTHO" | "PERSP"; stale: boolean;
  qa: { findings?: Finding[]; repairs?: unknown[] } | null;
  meta: {
    engine: string | null; resolution: [number, number] | null; base_policy: string | null;
    forward: number[] | null; ortho_scale: number | null; lens_mm: number | null;
    quadrant: string | null; elevation_deg: number | null; blender_version: string | null; seconds: number | null;
  } | null;
}
export interface Asset {
  id: string; project_id: string; code: string; name: string; category: string; fixture: string;
  mesh_status: string; model_revision: string | null; params: Record<string, unknown>;
  views: Record<ViewName, ViewInfo>;
  parent_id: string | null; source_revision_id: string | null; bbox: number[] | null; polygon: number[][] | null;
  count: number; visibility: string; confidence: number; scene_zone: string; notes: string;
  status: "confirmed" | "unconfirmed" | "disputed"; goal: OutputGoal; kind: string; has_crop: boolean;
}
export interface TreeNode extends Asset { children: TreeNode[] }
export interface Project {
  id: string; name: string; slug: string; scene_mode: SceneMode; output_goal: OutputGoal; base_mode: string;
  approved_source_id: string | null; style_recipe_id: string | null; created_at: number;
}
export interface Source {
  id: string; project_id: string; parent_id: string | null; origin: "IMPORTED" | "GENERATED" | "EDITED";
  sha256: string; width: number; height: number; format: string; bytes: number; approved: boolean; created_at: number;
  meta: Record<string, any>;
}
export interface Job {
  id: string; kind: string; state: string; progress: number; message: string; subject_id: string | null;
  result: Record<string, any> | null;
}
export interface BlenderInfo { found: boolean; path: string | null; version: string | null; device: string | null }
export interface Caps {
  generate: boolean; edit: boolean; mask_edit: boolean; aspect_ratios: string[]; resolutions: string[]; max_refs: number;
  max_variants: number; seed: boolean; negative_prompt: boolean; transparency: boolean; max_prompt_chars: number | null;
}
export interface ModelInfo { id: string; slug: string; label: string; status: string; status_detail: string; caps: Caps; source_url: string; last_checked: number | null }
export interface ProviderInfo { id: string; label: string; needs_key: boolean; configured: boolean; docs_url: string; models: ModelInfo[] }
export interface Style {
  id: string; label: string; owner: "app" | "user"; version: number; medium?: string; dimensionality: string;
  traits?: string; favorite?: boolean; strength?: number; palette?: string; lighting?: string; linework?: string;
  texture?: string; camera?: string; negative_constraints?: string[]; blend?: { style_id: string; weight: number }[];
}
export interface Estimate {
  estimate_id: string; expires_at: number; provider: string; provider_label?: string;
  model?: { id: string; label: string; slug?: string; status: string; status_detail?: string } | string | null;
  effective_prompt?: string; negative_prompt?: string; warnings: string[];
  cost: { usd_min: number | null; usd_max: number | null; unit: string; exact: boolean; note: string; free: boolean };
  requires_approval: boolean; calls?: number;
  outbound: { leaves_device: boolean; [k: string]: any };
  settings?: Record<string, any>;
}
export interface Budget { daily_cap_usd: number | null; monthly_cap_usd: number | null; spent_day_usd: number; spent_month_usd: number; local_only: boolean }
export interface ExportRun { id: string; status: string; path: string; folder: string | null; created_at: number; incomplete: Record<string, string[]>; override: { reason: string } | null }
export interface RenderOptions {
  resolution: number; engine: "CYCLES" | "WORKBENCH"; samples: number;
  base_policies: Partial<Record<ViewName, Policy>>;
  perspective_quadrant: "front_left" | "front_right" | "rear_left" | "rear_right";
  perspective_elevation_degrees: number; perspective_focal_mm: number;
  views?: ViewName[]; extras?: "top"[];
}
export interface ArtifactReport {
  view: string; sha256: string; stale: boolean; findings: Finding[]; masks: string[];
  metrics: Record<string, any>; settings: Record<string, any>; limits: string;
}
export interface Repair { id: string; view: string; kind: string; params: Record<string, any>; metrics: Record<string, any>; accepted: boolean; note: string }
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

/** The .cvbd file the app was launched with (desktop app only), parsed and validated. */
export async function launchProjectFile(): Promise<{ project_id: string; name: string } | null> {
  if (!isTauri()) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  const path = await invoke<string | null>("launch_file");
  if (!path) return null;
  const doc = JSON.parse(await invoke<string>("read_project_file", { path }));
  if (doc?.format !== "cozyverse-project" || typeof doc.project_id !== "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(doc.project_id)) {
    throw new Error("That is not a CozyVerse project file.");
  }
  return { project_id: doc.project_id, name: String(doc.name ?? "") };
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export function client(conn: Conn) {
  async function raw(method: string, path: string, body?: BodyInit | null, headers: Record<string, string> = {}) {
    const res = await fetch(conn.base + path, { method, headers: { Authorization: `Bearer ${conn.token}`, ...headers }, body });
    if (!res.ok) {
      let detail = res.statusText;
      try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : "Request rejected"; } catch { /* keep */ }
      throw new ApiError(res.status, detail);
    }
    return res;
  }
  async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await raw(method, path, body !== undefined ? JSON.stringify(body) : undefined,
      body !== undefined ? { "Content-Type": "application/json" } : {});
    return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
  }
  const get = <T,>(p: string) => req<T>("GET", p);
  const post = <T,>(p: string, b?: unknown) => req<T>("POST", p, b ?? {});

  return {
    health: () => get<{ status: string; blender: BlenderInfo }>("/health"),
    blender: () => get<BlenderInfo>("/settings/blender"),
    setBlender: (path: string) => req<BlenderInfo>("PUT", "/settings/blender", { path }),
    // projects
    projects: () => get<Project[]>("/projects"),
    project: (pid: string) => get<Project>(`/projects/${pid}`),
    createProject: (name: string, o: { fixtures?: string[]; scene_mode?: SceneMode; output_goal?: OutputGoal } = {}) =>
      post<Project>("/projects", { name, fixtures: o.fixtures ?? [], scene_mode: o.scene_mode ?? "DIORAMA", output_goal: o.output_goal ?? "3D_RECONSTRUCTION" }),
    patchProject: (pid: string, b: Partial<Pick<Project, "name" | "scene_mode" | "output_goal" | "base_mode" | "style_recipe_id">>) => req<Project>("PATCH", `/projects/${pid}`, b),
    addFixture: (pid: string, fixture: string) => post<Asset>(`/projects/${pid}/fixtures`, { fixture }),
    assets: (pid: string) => get<Asset[]>(`/projects/${pid}/assets`),
    tree: (pid: string) => get<TreeNode[]>(`/projects/${pid}/inventory`),
    // sources
    sources: (pid: string) => get<Source[]>(`/projects/${pid}/sources`),
    source: (sid: string) => get<Source>(`/sources/${sid}`),
    lineage: (sid: string) => get<Source[]>(`/sources/${sid}/lineage`),
    async uploadSource(pid: string, file: File): Promise<Source> {
      const res = await raw("POST", `/projects/${pid}/sources`, file, { "Content-Type": file.type || "application/octet-stream" });
      return (await res.json()) as Source;
    },
    approveSource: (pid: string, sid: string) => post<Source>(`/projects/${pid}/sources/${sid}/approve`),
    editLocal: (pid: string, sid: string, op: Record<string, unknown>) => post<Source>(`/projects/${pid}/sources/${sid}/edit-local`, op),
    // inventory
    createItem: (pid: string, b: Record<string, unknown>) => post<Asset>(`/projects/${pid}/inventory`, b),
    patchItem: (aid: string, b: Record<string, unknown>) => req<Asset>("PATCH", `/inventory/${aid}`, b),
    deleteItem: (aid: string) => req<void>("DELETE", `/inventory/${aid}`),
    addInstance: (aid: string, bbox: number[]) => post<{ id: string }>(`/inventory/${aid}/instances`, { bbox }),
    mergeItems: (pid: string, ids: string[], into: string) => post<Asset>(`/projects/${pid}/inventory/merge`, { ids, into }),
    confirmItems: (pid: string, ids: string[] | null, min_confidence = 0) => post<{ confirmed: number }>(`/projects/${pid}/inventory/confirm`, { ids, min_confidence }),
    audit: (pid: string) => get<{ item_count: number; tiny_items: any[]; unconfirmed: any[]; low_confidence: string[]; empty_zones: { zone: number[] }[]; hint: string }>(`/projects/${pid}/inventory/audit`),
    cropUrl(aid: string, kind: "crop" | "mask" = "crop") { return this.blobUrl(`/inventory/${aid}/crop?kind=${kind}`); },
    // detection
    detectEstimate: (pid: string, b: { provider: string; model?: string; tiles: number }) => post<Estimate>(`/projects/${pid}/detect/estimate`, b),
    detect: (pid: string, estimate_id: string, approved: boolean) => post<{ job_id: string }>(`/projects/${pid}/detect`, { estimate_id, approved }),
    aiProviders: () => get<{ vision: { id: string; label: string; needs_key: boolean; needs_model: boolean; configured: boolean }[]; mesh: ProviderInfo[] }>("/ai/providers"),
    // proxy, mesh
    buildProxy: (aid: string, params?: Record<string, unknown>) => post<{ job_id: string }>(`/assets/${aid}/build-proxy`, params ? { params } : {}),
    async importMesh(aid: string, file: File): Promise<Asset> {
      const res = await raw("POST", `/assets/${aid}/mesh`, file, { "Content-Type": "model/gltf-binary" });
      return (await res.json()) as Asset;
    },
    meshOrientation: (aid: string, yaw_deg: number, target_height: number | null) => req<Asset>("PUT", `/assets/${aid}/mesh/orientation`, { yaw_deg, target_height }),
    approveMesh: (aid: string) => post<Asset>(`/assets/${aid}/mesh/approve`),
    meshEstimate: (aid: string, b: { provider_id: string; model_id: string; options: Record<string, unknown> }) => post<Estimate>(`/assets/${aid}/mesh-generate/estimate`, b),
    meshGenerate: (aid: string, estimate_id: string, approved: boolean) => post<{ job_id: string }>(`/assets/${aid}/mesh-generate`, { estimate_id, approved }),
    // rendering
    renderFive: (aid: string, o: RenderOptions) => post<{ job_id: string }>(`/assets/${aid}/render-five`, {
      resolution: o.resolution, engine: o.engine, samples: o.samples, background: "white", base_policies: o.base_policies,
      perspective_quadrant: o.perspective_quadrant, perspective_elevation_degrees: o.perspective_elevation_degrees,
      perspective_focal_mm: o.perspective_focal_mm, ...(o.views ? { views: o.views } : {}), ...(o.extras ? { extras: o.extras } : {}),
    }),
    review: (aid: string, view: ViewName, status: "PASSED" | "FAILED" | "NEEDS_REVIEW") => post<Asset>(`/assets/${aid}/views/${view}/review`, { status }),
    // artifact qa
    analyzeAsset: (aid: string, settings?: Record<string, unknown>) => post<{ views: Record<string, ArtifactReport>; consistency: Finding[] }>(`/assets/${aid}/artifacts/analyze`, settings),
    analyzeView: (aid: string, v: ViewName, settings?: Record<string, unknown>) => post<ArtifactReport>(`/assets/${aid}/views/${v}/artifacts/analyze`, settings),
    artifacts: (aid: string, v: ViewName) => get<ArtifactReport>(`/assets/${aid}/views/${v}/artifacts`),
    repairs: (aid: string) => get<Repair[]>(`/assets/${aid}/repairs`),
    previewRepair: (aid: string, v: ViewName, kind: "cast" | "noise", strength: number) => post<Repair & { files: string[] }>(`/assets/${aid}/views/${v}/repairs/preview`, { kind, strength }),
    acceptRepair: (aid: string, rid: string, note: string) => post<ArtifactReport>(`/assets/${aid}/repairs/${rid}/accept`, { note }),
    rejectRepair: (aid: string, rid: string) => post<void>(`/assets/${aid}/repairs/${rid}/reject`),
    revertRepair: (aid: string, v: ViewName) => post<ArtifactReport>(`/assets/${aid}/views/${v}/repairs/revert`),
    // jobs, export
    job: (jid: string) => get<Job>(`/jobs/${jid}`),
    cancel: (jid: string) => req<Job>("DELETE", `/jobs/${jid}`),
    exportProject: (pid: string, o: { include_contact_sheet?: boolean; override_critical?: boolean; override_reason?: string } = {}) => post<{ job_id: string }>(`/projects/${pid}/export`, o),
    projectFile: (pid: string) => get<{ format: string; version: number; project_id: string; name: string; slug: string }>(`/projects/${pid}/file`),
    exports: (pid: string) => get<ExportRun[]>(`/projects/${pid}/exports`),
    // providers, styles, costs
    providers: () => get<ProviderInfo[]>("/providers"),
    setKey: (pid: string, key: string) => req<void>("PUT", `/providers/${pid}/key`, { key }),
    deleteKey: (pid: string) => req<void>("DELETE", `/providers/${pid}/key`),
    refreshProvider: (pid: string) => post<{ ok: boolean; detail: string; models: ModelInfo[] }>(`/providers/${pid}/refresh`),
    recommend: (goal: string) => get<{ model_id: string; label: string; status: string; reason: string }[]>(`/models/recommend?goal=${goal}`),
    budget: () => get<Budget>("/settings/budget"),
    setBudget: (b: Partial<{ daily_cap_usd: number | null; monthly_cap_usd: number | null; local_only: boolean }>) => req<Budget>("PUT", "/settings/budget", b),
    setPrice: (model_id: string, usd: number | null) => req<void>("PUT", "/settings/prices", { model_id, usd }),
    usage: () => get<any[]>("/settings/usage"),
    styles: () => get<Style[]>("/styles"),
    createStyle: (b: Record<string, unknown>) => post<Style>("/styles", b),
    updateStyle: (id: string, b: Record<string, unknown>) => req<Style>("PUT", `/styles/${id}`, b),
    deleteStyle: (id: string) => req<void>("DELETE", `/styles/${id}`),
    favoriteStyle: (id: string, on: boolean) => post<void>(`/styles/${id}/favorite`, { on }),
    exportStyles: () => get<any>("/styles/export"),
    importStyles: (doc: unknown) => post<Style[]>("/styles/import", doc),
    compileStyle: (b: Record<string, unknown>) => post<{ prompt: string; negative_prompt: string; warnings: string[] }>("/styles/compile", b),
    // generation
    estimateGenerate: (pid: string, b: Record<string, unknown>) => post<Estimate>(`/projects/${pid}/generate/estimate`, b),
    generate: (pid: string, estimate_id: string, approved: boolean, mask?: string) => post<{ job_id: string }>(`/projects/${pid}/generate`, { estimate_id, approved, ...(mask ? { mask_png_b64: mask } : {}) }),
    // binary
    async blobUrl(path: string): Promise<string> {
      const res = await raw("GET", path);
      return URL.createObjectURL(await res.blob());
    },
    extraUrl(aid: string, name: "top") { return this.blobUrl(`/assets/${aid}/extras/${name}`); },
    repairUrl(aid: string, rid: string, which: "before" | "after" | "diff" | "mask") { return this.blobUrl(`/assets/${aid}/repairs/${rid}/image?which=${which}`); },
    imageUrl(aid: string, view: ViewName) { return this.blobUrl(`/assets/${aid}/views/${view}/image`); },
    sourceUrl(sid: string, thumb = false) { return this.blobUrl(`/sources/${sid}/image${thumb ? "?thumb=true" : ""}`); },
  };
}
export type Api = ReturnType<typeof client>;
