import { useEffect, useState } from "react";
import type { OutputGoal, Project, SceneMode } from "../api";
import { Chip, FileButton, Seg, msg, useApp, when } from "../ui";

const GOAL_LABEL: Record<OutputGoal, string> = {
  "2D_LAYER_EXPORT": "2D layers", REFERENCE_ONLY: "Reference only", "3D_PROXY": "3D proxy",
  "3D_RECONSTRUCTION": "3D + five views", SCENE_REASSEMBLY: "Scene reassembly",
};

export function Home() {
  const { api, goto, setProject, setError, setSelectedSource, blender } = useApp();
  const [projects, setProjects] = useState<Project[]>([]);
  const [importMode, setImportMode] = useState<SceneMode>("DIORAMA");
  const [working, setWorking] = useState(false);

  useEffect(() => { api.projects().then(setProjects).catch((e) => setError(msg(e))); }, [api, setError]);

  const start = async (mode: SceneMode) => {
    setWorking(true); setError(null);
    try {
      const p = await api.createProject(mode === "DIORAMA" ? "Untitled diorama" : "Untitled image", {
        scene_mode: mode, output_goal: mode === "DIORAMA" ? "3D_RECONSTRUCTION" : "2D_LAYER_EXPORT" });
      setProject(p); setSelectedSource(null); goto("create");
    } catch (e) { setError(msg(e)); } finally { setWorking(false); }
  };
  const importImage = async (file: File) => {
    setWorking(true); setError(null);
    try {
      const name = file.name.replace(/\.[^.]+$/, "").slice(0, 60) || "Imported image";
      const p = await api.createProject(name, { scene_mode: importMode, output_goal: importMode === "DIORAMA" ? "3D_RECONSTRUCTION" : "2D_LAYER_EXPORT" });
      const s = await api.uploadSource(p.id, file);
      setProject(p); setSelectedSource(s.id); goto("editor");
    } catch (e) { setError(msg(e)); } finally { setWorking(false); }
  };
  const sample = async () => {
    setWorking(true); setError(null);
    try {
      const p = await api.createProject("Rainy Night Market", { fixtures: ["bar", "master_base"], scene_mode: "DIORAMA" });
      setProject(p); goto("studio");
    } catch (e) { setError(msg(e)); } finally { setWorking(false); }
  };
  const open = (p: Project) => { setProject(p); setSelectedSource(null); goto(p.approved_source_id ? "decompose" : "create"); };

  return (
    <div className="page">
      <header className="hero">
        <h1>Turn one world into a reusable universe of assets.</h1>
        <p>Create or import an image, break it into assets, and render four true orthographic views plus one perspective view of each from the same 3D model.</p>
      </header>

      <section className="two-cards" aria-label="Start">
        <button className="big-card" disabled={working} onClick={() => void start("DIORAMA")}>
          <b>Create Diorama</b>
          <span>A miniature scene on a display base. Generate it from a prompt in any art style, then break it apart.</span>
          <em>Starts without an image</em>
        </button>
        <button className="big-card" disabled={working} onClick={() => void start("REGULAR_IMAGE")}>
          <b>Create Regular Image</b>
          <span>A painting, photo, character, poster or scene. No base is added. Extract 2D layers or build 3D references.</span>
          <em>Starts without an image</em>
        </button>
      </section>

      <section className="row-actions" aria-label="Other ways to start">
        <div className="import-box">
          <FileButton label="Import Image…" accept="image/png,image/jpeg,image/webp" onFile={(f) => void importImage(f)} disabled={working} />
          <Seg label="Image type" value={importMode} onChange={setImportMode}
            options={[{ value: "DIORAMA", label: "Diorama" }, { value: "REGULAR_IMAGE", label: "Regular image" }]} />
        </div>
        <button className="btn" disabled={working || !blender?.found} onClick={() => void sample()}
          title={blender?.found ? "BAR building and master base, ready to build and render" : "Needs Blender to render"}>Open sample project</button>
      </section>

      <section aria-label="Recent projects" className="recent">
        <h2>Projects</h2>
        {projects.length === 0 ? <p className="hint">No projects yet. Start with a diorama, a regular image or an import.</p> : (
          <ul className="plist">
            {projects.map((p) => (
              <li key={p.id}>
                <div><b>{p.name}</b><small>{when(p.created_at)}</small></div>
                <Chip>{p.scene_mode === "DIORAMA" ? "Diorama" : "Regular image"}</Chip>
                <Chip>{GOAL_LABEL[p.output_goal]}</Chip>
                {p.approved_source_id && <Chip tone="ok">Source approved</Chip>}
                <button className="btn sm" onClick={() => open(p)}>Open</button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
