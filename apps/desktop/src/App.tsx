import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Api, ApiError, Asset, BlenderInfo, Job, Policy, Project, RenderOptions, ViewName, VIEWS,
  canOpenFolder, client, getConnection, openFolder,
} from "./api";
import { ViewCard } from "./ViewCard";
import splash from "./assets/splash.webp";
import logo from "./assets/logo.png";

const POLICIES: { value: Policy; label: string }[] = [
  { value: "none", label: "None (asset only)" },
  { value: "building_foundation", label: "Building foundation" },
  { value: "local_plinth", label: "Local plinth" },
  { value: "scene_context", label: "Scene context" },
  { value: "full_master_base", label: "Full master base" },
];
const ACTIVE = new Set(["NEW", "QUEUED", "RUNNING"]);

export default function App() {
  const [api, setApi] = useState<Api | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);
  const [splashDone, setSplashDone] = useState(false);
  const [blender, setBlender] = useState<BlenderInfo | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [exportJob, setExportJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [elevPolicy, setElevPolicy] = useState<Policy>("building_foundation");
  const [perspPolicy, setPerspPolicy] = useState<Policy>("local_plinth");
  const [opts, setOpts] = useState({ resolution: 1024, engine: "CYCLES" as RenderOptions["engine"], samples: 64,
    quadrant: "front_left" as RenderOptions["perspective_quadrant"], elevation: 30, focal: 50 });
  const [plinth, setPlinth] = useState({ margin: 0.5, height: 0.2 });
  const [blenderPath, setBlenderPath] = useState("");
  const pollRef = useRef<number | null>(null);

  const boot = useCallback(async () => {
    setBootError(null);
    try {
      const a = client(await getConnection());
      let h: Awaited<ReturnType<typeof a.health>> | null = null;
      for (let i = 0; i < 12 && !h; i++) {
        try { h = await a.health(); } catch (e) { if (i === 11) throw e; await new Promise((r) => setTimeout(r, 400)); }
      }
      if (!h) throw new Error("worker did not respond");
      setBlender(h.blender);
      const ps = await a.projects();
      setApi(a);
      if (ps.length) setProject(ps[0]);
      setLoaded(true);
    } catch (e) {
      setBootError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => { void boot(); const t = setTimeout(() => setSplashDone(true), 1600); return () => clearTimeout(t); }, [boot]);

  const refresh = useCallback(async () => {
    if (!api || !project) return;
    const raw = await api.assets(project.id);
    // buildings first, the master base last
    const list = [...raw].sort((x, y) => Number(x.category === "master_base") - Number(y.category === "master_base") || x.code.localeCompare(y.code));
    setAssets(list);
    setSelectedId((cur) => cur && list.some((x) => x.id === cur) ? cur : list[0]?.id ?? null);
  }, [api, project]);
  useEffect(() => { void refresh().catch((e) => setError(String(e.message ?? e))); }, [refresh]);

  const asset = useMemo(() => assets.find((a) => a.id === selectedId) ?? null, [assets, selectedId]);
  useEffect(() => {
    if (!asset) return;
    const building = asset.category === "building";
    setElevPolicy(building ? "building_foundation" : "none");
    setPerspPolicy(building ? "local_plinth" : "none");
  }, [asset?.id, asset?.category]); // eslint-disable-line react-hooks/exhaustive-deps

  const track = useCallback((jobId: string, kind: "work" | "export") => {
    if (!api) return;
    if (pollRef.current) window.clearInterval(pollRef.current);
    const tick = async () => {
      try {
        const j = await api.job(jobId);
        (kind === "export" ? setExportJob : setJob)(j);
        await refresh();
        if (!ACTIVE.has(j.state) && pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
      } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    };
    void tick();
    pollRef.current = window.setInterval(tick, 600);
  }, [api, refresh]);
  useEffect(() => () => { if (pollRef.current) window.clearInterval(pollRef.current); }, []);

  const busy = !!job && ACTIVE.has(job.state) || !!exportJob && ACTIVE.has(exportJob.state);
  const run = async (fn: () => Promise<{ job_id: string }>, kind: "work" | "export" = "work") => {
    setError(null);
    try { track((await fn()).job_id, kind); }
    catch (e) { setError(e instanceof ApiError ? e.message : String(e)); }
  };

  const renderOptions = (): RenderOptions => ({
    resolution: opts.resolution, engine: opts.engine, samples: opts.samples,
    base_policies: { front: elevPolicy, back: elevPolicy, left: elevPolicy, right: elevPolicy, perspective: perspPolicy },
    perspective_quadrant: opts.quadrant, perspective_elevation_degrees: opts.elevation, perspective_focal_mm: opts.focal,
  });

  const createSample = async () => {
    if (!api) return;
    setError(null);
    try {
      const p = await api.createProject("Rainy Night Market", ["bar", "master_base"]);
      setProject(p);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };

  const saveBlender = async () => {
    if (!api) return;
    try { setBlender(await api.setBlender(blenderPath)); setError(null); }
    catch { setError("That path is not a working Blender executable."); }
  };

  const hasMesh = asset?.mesh_status === "PROXY_APPROVED";
  const pending = (v: ViewName) => !!asset?.views[v].qa?.findings?.some((f) => f.code === "RENDER_PENDING");
  const anyStale = asset ? VIEWS.some((v) => asset.views[v].stale && !pending(v)) : false;
  const mixed = elevPolicy !== perspPolicy;
  const booted = loaded && splashDone;

  return (
    <>
      <div className={`splash ${booted ? "out" : ""}`} aria-hidden={booted}>
        <img src={splash} alt="CozyVerse Breakdown. Create. Decompose. Rebuild." />
        {!bootError && <div className="load"><i /></div>}
        {bootError && (
          <div className="notice bad" role="alert" style={{ position: "absolute", bottom: "6%", maxWidth: 560 }}>
            Could not reach the local worker: {bootError}{" "}
            <button className="btn sm" onClick={() => void boot()}>Retry</button>
          </div>
        )}
      </div>

      <div className="shell">
        <nav className="sidebar" aria-label="Main">
          <div className="brand">
            <img src={logo} alt="" />
            <div><b>CozyVerse</b><span>Breakdown</span></div>
          </div>
          <div className="nav-h">Workspace</div>
          <button className="nav-item" aria-current="true"><span>Five-View Studio</span></button>
          <div className="nav-h">Assets</div>
          {assets.map((a) => (
            <button key={a.id} className="nav-item" aria-current={a.id === selectedId}
              onClick={() => setSelectedId(a.id)} title={`${a.code} · ${a.name}`}>
              <span>{a.name}<br /><small>{a.code}</small></span>
              <span className={`chip ${a.mesh_status === "PROXY_APPROVED" ? "ok" : ""}`}>
                {a.mesh_status === "PROXY_APPROVED" ? "Proxy" : "No mesh"}
              </span>
            </button>
          ))}
          {!assets.length && <p className="hint" style={{ padding: "0 8px" }}>No assets yet.</p>}
        </nav>

        <header className="topbar">
          <div className="crumbs">Projects / <b>{project?.name ?? "No project"}</b>{asset && <> / <b>{asset.code}</b></>}</div>
          <span className="spacer" />
          <span className={`chip ${blender?.found ? "ok" : "bad"}`} title={blender?.path ?? "Blender not found"}>
            <i className="dot" />{blender?.found ? `Blender ${blender.version} · ${blender.device}` : "Blender not found"}
          </span>
          <span className={`chip ${busy ? "info" : ""}`}><i className="dot" />{busy ? "Working" : "Idle"}</span>
          <span className="chip" title="Nothing leaves this computer in Phase 0">Offline · local only</span>
        </header>

        <main className="main">
          {!project && loaded && (
            <section className="welcome">
              <h1>Turn one world into a reusable universe of assets.</h1>
              <p>Phase 0 proves the camera contract: four true orthographic elevations and one perspective render, each a separate Blender render from the same 3D object.</p>
              <button className="btn primary hero" onClick={() => void createSample()}>Open sample project (BAR + base)</button>
              {error && <div className="notice bad" role="alert">{error}</div>}
            </section>
          )}

          {project && asset && (
            <>
              <div className="studio-head">
                <div>
                  <h1>{asset.name} <small style={{ color: "var(--muted)", fontWeight: 500 }}>{asset.code}</small></h1>
                  <p>Four orthographic camera renders plus one perspective render, all from the same 3D object.</p>
                </div>
                <div className="actions">
                  <button className={`btn ${hasMesh && !anyStale ? "" : "primary"}`} disabled={busy || !blender?.found}
                    onClick={() => void run(() => api!.buildProxy(asset.id, asset.category === "building"
                      ? { plinth_margin: plinth.margin, plinth_height: plinth.height } : undefined))}>
                    {hasMesh ? "Rebuild Proxy" : "Build Proxy"}
                  </button>
                  <button className={`btn hero ${hasMesh ? "primary" : ""}`} disabled={busy || !hasMesh || !blender?.found}
                    title={hasMesh ? "Render all five views" : "Cannot produce verified side views without a 3D model. Build a proxy or import a mesh."}
                    onClick={() => void run(() => api!.renderFive(asset.id, renderOptions()))}>
                    Render 5 Views
                  </button>
                  <button className="btn" disabled={busy} onClick={() => void run(() => api!.exportProject(project.id), "export")}>Export ZIP</button>
                  <button className="btn" disabled={!exportJob?.result?.folder || !canOpenFolder()}
                    title={canOpenFolder() ? "Open the export folder" : "Available in the desktop app"}
                    onClick={() => exportJob?.result?.folder && void openFolder(exportJob.result.folder)}>Open Folder</button>
                </div>
              </div>

              {!blender?.found && <div className="notice bad" role="alert">Blender was not found. Set its path in Render settings to enable proxy building and rendering.</div>}
              {!hasMesh && blender?.found && <div className="notice warn">Cannot produce verified side views without a 3D model. Build a proxy or import a mesh.</div>}
              {anyStale && <div className="notice warn">The model changed after these views were rendered. Re-render all five before approving or exporting.</div>}
              {error && <div className="notice bad" role="alert">{error}</div>}
              {asset.category === "building" && <div className="notice">The back and sides of this building are not visible in your source. Review inferred geometry before approval.</div>}

              <section className="views" aria-label="Five views">
                {VIEWS.map((v: ViewName) => (
                  <ViewCard key={v} asset={asset} view={v} api={api!} disabled={busy}
                    onReview={async (status) => {
                      try { await api!.review(asset.id, v, status); await refresh(); setError(null); }
                      catch (e) { setError(e instanceof Error ? e.message : String(e)); }
                    }} />
                ))}
              </section>

              <aside className="panel" aria-label="Render settings">
                <h2>Render settings</h2>
                <label className="field"><span>Elevation base policy (front/back/left/right)</span>
                  <select value={elevPolicy} onChange={(e) => setElevPolicy(e.target.value as Policy)}>
                    {POLICIES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                  </select></label>
                <label className="field"><span>Perspective base policy</span>
                  <select value={perspPolicy} onChange={(e) => setPerspPolicy(e.target.value as Policy)}>
                    {POLICIES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                  </select></label>
                {mixed && <p className="hint">Elevations and perspective use different bases. Fine for presentation; use one policy for dimension-matched 3D references.</p>}
                <div className="two">
                  <label className="field"><span>Resolution</span>
                    <select value={opts.resolution} onChange={(e) => setOpts({ ...opts, resolution: +e.target.value })}>
                      {[512, 1024, 1536, 2048].map((r) => <option key={r} value={r}>{r} × {r}</option>)}
                    </select></label>
                  <label className="field"><span>Engine</span>
                    <select value={opts.engine} onChange={(e) => setOpts({ ...opts, engine: e.target.value as RenderOptions["engine"] })}>
                      <option value="CYCLES">Cycles (final)</option>
                      <option value="WORKBENCH">Workbench (fast draft)</option>
                    </select></label>
                </div>
                <label className="field"><span>Perspective corner</span>
                  <select value={opts.quadrant} onChange={(e) => setOpts({ ...opts, quadrant: e.target.value as typeof opts.quadrant })}>
                    <option value="front_left">Front-left</option><option value="front_right">Front-right</option>
                    <option value="rear_left">Rear-left</option><option value="rear_right">Rear-right</option>
                  </select></label>
                <div className="two">
                  <label className="field"><span>Elevation (°)</span>
                    <input type="number" min={0} max={89} value={opts.elevation} onChange={(e) => setOpts({ ...opts, elevation: Math.min(89, Math.max(0, +e.target.value)) })} /></label>
                  <label className="field"><span>Focal length (mm)</span>
                    <input type="number" min={10} max={300} value={opts.focal} onChange={(e) => setOpts({ ...opts, focal: Math.min(300, Math.max(10, +e.target.value)) })} /></label>
                </div>
                {asset.category === "building" && (
                  <div className="two">
                    <label className="field"><span>Plinth margin (m)</span>
                      <input type="number" step={0.1} min={0} max={5} value={plinth.margin} onChange={(e) => setPlinth({ ...plinth, margin: Math.min(5, Math.max(0, +e.target.value)) })} /></label>
                    <label className="field"><span>Plinth height (m)</span>
                      <input type="number" step={0.05} min={0.02} max={1} value={plinth.height} onChange={(e) => setPlinth({ ...plinth, height: Math.min(1, Math.max(0.02, +e.target.value)) })} /></label>
                  </div>
                )}
                <p className="hint">Plinth changes apply on Rebuild Proxy, and invalidate existing renders.</p>
                <h2>Blender</h2>
                <label className="field"><span>Executable path</span>
                  <input value={blenderPath} placeholder={blender?.path ?? "C:\\Program Files\\Blender Foundation\\…\\blender.exe"}
                    onChange={(e) => setBlenderPath(e.target.value)} /></label>
                <button className="btn sm" disabled={!blenderPath} onClick={() => void saveBlender()}>Test and save</button>
              </aside>
            </>
          )}
        </main>

        <footer className="drawer" aria-live="polite">
          {job || exportJob ? (
            <>
              {job && <JobLine job={job} onCancel={() => api && void api.cancel(job.id).then(setJob)} />}
              {exportJob && !ACTIVE.has(exportJob.state) && exportJob.result?.path && (
                <span className="msg"><b>Export {exportJob.result.status}</b> · {exportJob.result.path}</span>
              )}
              {exportJob && ACTIVE.has(exportJob.state) && <span className="msg"><b>Exporting…</b></span>}
            </>
          ) : <span className="msg">No jobs running. Renders and exports are local Blender jobs and cost nothing.</span>}
        </footer>
      </div>
    </>
  );
}

function JobLine({ job, onCancel }: { job: Job; onCancel: () => void }) {
  const active = ACTIVE.has(job.state);
  const tone = job.state === "SUCCEEDED" ? "ok" : active ? "info" : job.state === "CANCELLED" ? "warn" : "bad";
  return (
    <>
      <span className={`chip ${tone}`}>{job.state}</span>
      <span className="msg"><b>{job.kind.replace("_", " ")}</b> · {job.message}</span>
      {active && <div className="bar"><i style={{ width: `${Math.round(job.progress * 100)}%` }} /></div>}
      {active && <button className="btn sm danger" onClick={onCancel}>Cancel</button>}
    </>
  );
}
