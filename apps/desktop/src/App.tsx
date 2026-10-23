import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, Asset, BlenderInfo, Job, Project, client, getConnection, launchProjectFile, type Api } from "./api";
import { Docs, QuickStart } from "./Help";
import { Splash, splashDisabled } from "./Splash";
import { AppCtx, Chip, Ctx, Notice, Screen, msg } from "./ui";
import logo from "./assets/logo.png";
import { Home } from "./screens/Home";
import { Create } from "./screens/Create";
import { Editor } from "./screens/Editor";
import { Decompose } from "./screens/Decompose";
import { Studio } from "./screens/Studio";
import { Styles } from "./screens/Styles";
import { Models } from "./screens/Models";
import { Exports } from "./screens/Exports";
import { Settings, applyMotion } from "./screens/Settings";

const ACTIVE = new Set(["NEW", "QUEUED", "RUNNING"]);
const NAV: { id: Screen; label: string; group: string }[] = [
  { id: "home", label: "Home", group: "Start" },
  { id: "create", label: "Create", group: "Image" },
  { id: "editor", label: "Editor", group: "Image" },
  { id: "decompose", label: "Decompose", group: "Break down" },
  { id: "studio", label: "3D Studio", group: "Break down" },
  { id: "exports", label: "Exports", group: "Break down" },
  { id: "styles", label: "Style Library", group: "Library" },
  { id: "models", label: "Models and Costs", group: "Library" },
  { id: "settings", label: "Settings", group: "Library" },
];

export default function App() {
  const [api, setApi] = useState<Api | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);
  const [splashOpen, setSplashOpen] = useState(() => !splashDisabled());
  const [quick, setQuick] = useState(false);
  const [docs, setDocs] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [blender, setBlender] = useState<BlenderInfo | null>(null);
  const [project, setProjectState] = useState<Project | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedSource, setSelectedSource] = useState<string | null>(null);
  const [styleChoice, setStyleChoice] = useState<string | null>(null);
  const [screen, setScreen] = useState<Screen>("home");
  const [job, setJob] = useState<Job | null>(null);
  const [jobLabel, setJobLabel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const boot = useCallback(async () => {
    setBootError(null);
    try {
      const a = client(await getConnection());
      let h: Awaited<ReturnType<typeof a.health>> | null = null;
      const deadline = Date.now() + 6000;   // the host may call before uvicorn is serving; refused connections are slow on Windows
      for (;;) {
        try { h = await a.health(); break; }
        catch (e) { if (Date.now() > deadline) throw e; await new Promise((r) => setTimeout(r, 300)); }
      }
      setBlender(h.blender);
      setApi(a);
      setLoaded(true);
      try {                                   // opened by double-clicking a .cvbd file
        const f = await launchProjectFile();
        if (f) {
          try { const p = await a.project(f.project_id); setProjectState(p); setScreen(p.approved_source_id ? "decompose" : "create"); }
          catch { setError(`The project "${f.name}" is not stored on this computer. A .cvbd file only points to a project on the computer that saved it. Use Export ZIP to share work.`); }
        }
      } catch (e) { setError(msg(e)); }
    } catch (e) { setBootError(msg(e)); }
  }, []);
  useEffect(() => { void boot(); }, [boot]);
  useEffect(() => { try { applyMotion(localStorage.getItem("cv.motion.off") === "true"); } catch { /* blocked */ } }, []);
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "F1") { e.preventDefault(); setDocs(true); } };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  const closeSplash = useCallback(() => setSplashOpen(false), []);

  const setProject = useCallback((p: Project | null) => { setProjectState(p); setSelectedId(null); }, []);
  const refresh = useCallback(async () => {
    if (!api || !project) { setAssets([]); return; }
    const list = await api.assets(project.id);
    list.sort((x, y) => Number(x.category === "master_base") - Number(y.category === "master_base") || x.code.localeCompare(y.code));
    setAssets(list);
    setSelectedId((cur) => (cur && list.some((x) => x.id === cur) ? cur : null));
    try { setProjectState(await api.project(project.id)); } catch { /* keep */ }
  }, [api, project?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { void refresh().catch((e) => setError(msg(e))); }, [project?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const busy = !!job && ACTIVE.has(job.state);
  const run: Ctx["run"] = useCallback(async (label, start, onDone) => {
    if (!api) return;
    setError(null); setNotice(null);
    let jid: string;
    try { jid = (await start()).job_id; } catch (e) { setError(e instanceof ApiError ? e.message : msg(e)); return; }
    setJobLabel(label);
    if (pollRef.current) window.clearInterval(pollRef.current);
    await new Promise<void>((resolve) => {
      const tick = async () => {
        try {
          const j = await api.job(jid);
          setJob(j);
          if (!ACTIVE.has(j.state)) {
            if (pollRef.current) window.clearInterval(pollRef.current);
            pollRef.current = null;
            if (j.state === "FAILED" || j.state === "BLOCKED") setError(j.message);
            else if (j.state === "CANCELLED") setNotice("Cancelled. Anything already finished was kept.");
            try { await refresh(); await onDone?.(j); } catch (e) { setError(msg(e)); }
            resolve();
          }
        } catch (e) { setError(msg(e)); if (pollRef.current) window.clearInterval(pollRef.current); resolve(); }
      };
      void tick();
      pollRef.current = window.setInterval(tick, 500);
    });
  }, [api, refresh]);
  useEffect(() => () => { if (pollRef.current) window.clearInterval(pollRef.current); }, []);

  const ctx: Ctx | null = useMemo(() => api ? ({
    api, blender, setBlender, project, setProject, assets, refresh, selectedId, setSelectedId, goto: setScreen, screen,
    error, setError, notice, setNotice, run, busy, job, selectedSource, setSelectedSource, styleChoice, setStyleChoice,
  }) : null, [api, blender, project, setProject, assets, refresh, selectedId, screen, error, notice, run, busy, job, selectedSource, styleChoice]);

  const page = (() => {
    switch (screen) {
      case "home": return <Home />;
      case "create": return <Create />;
      case "editor": return <Editor />;
      case "decompose": return <Decompose />;
      case "studio": return <Studio />;
      case "styles": return <Styles />;
      case "models": return <Models />;
      case "exports": return <Exports />;
      case "settings": return <Settings />;
    }
  })();

  return (
    <>
      {splashOpen && <Splash ready={loaded} error={bootError} onRetry={() => void boot()} onClose={closeSplash} />}
      {quick && <QuickStart onClose={() => setQuick(false)} onDocs={() => { setQuick(false); setDocs(true); }} />}
      {docs && <Docs onClose={() => setDocs(false)} />}

      <div className="shell">
        <nav className="sidebar" aria-label="Main">
          <div className="brand"><img src={logo} alt="" /><div><b>CozyVerse</b><span>Breakdown</span></div></div>
          {["Start", "Image", "Break down", "Library"].map((g) => (
            <div key={g}>
              <div className="nav-h">{g}</div>
              {NAV.filter((n) => n.group === g).map((n) => (
                <button key={n.id} className="nav-item" aria-current={screen === n.id} onClick={() => setScreen(n.id)}><span>{n.label}</span></button>
              ))}
            </div>
          ))}
        </nav>

        <header className="topbar">
          <div className="crumbs">{project && screen !== "home" ? <>Project / <b>{project.name}</b></> : <b>CozyVerse Breakdown</b>}</div>
          <span className="spacer" />
          <span className={`chip ${blender?.found ? "ok" : "bad"}`} title={blender?.path ?? "Blender not found"}>
            <i className="dot" />{blender?.found ? `Blender ${blender.version} · ${blender.device}` : "Blender not found"}</span>
          <span className={`chip ${busy ? "info" : ""}`}><i className="dot" />{busy ? "Working" : "Idle"}</span>
          <span className="chip" title="Nothing leaves this computer unless you approve a provider call">Offline · local only</span>
          <button className="btn sm" onClick={() => setQuick(true)}>Quick start</button>
          <button className="btn sm" onClick={() => setDocs(true)} title="Documentation (F1)">Documentation</button>
        </header>

        <main className="main">
          {bootError && !splashOpen && (
            <Notice tone="bad">Could not reach the local worker: {bootError} <button className="btn sm" onClick={() => void boot()}>Retry</button></Notice>
          )}
          {ctx && (
            <AppCtx.Provider value={ctx}>
              {error && <div className="banner bad" role="alert"><span>{error}</span><button className="icon-btn" aria-label="Dismiss" onClick={() => setError(null)}>×</button></div>}
              {notice && <div className="banner" role="status"><span>{notice}</span><button className="icon-btn" aria-label="Dismiss" onClick={() => setNotice(null)}>×</button></div>}
              {page}
            </AppCtx.Provider>
          )}
        </main>

        <footer className="drawer" aria-live="polite">
          {job ? (
            <>
              <Chip tone={job.state === "SUCCEEDED" ? "ok" : ACTIVE.has(job.state) ? "info" : job.state === "CANCELLED" ? "warn" : "bad"}>{job.state}</Chip>
              <span className="msg"><b>{jobLabel || job.kind.replace(/_/g, " ")}</b> · {job.message}</span>
              {ACTIVE.has(job.state) && <div className="bar"><i style={{ width: `${Math.round(job.progress * 100)}%` }} /></div>}
              {ACTIVE.has(job.state) && api && <button className="btn sm danger" onClick={() => void api.cancel(job.id)}>Cancel</button>}
            </>
          ) : <span className="msg">No jobs running. Local renders and the built-in generator cost nothing.</span>}
        </footer>
      </div>
    </>
  );
}
