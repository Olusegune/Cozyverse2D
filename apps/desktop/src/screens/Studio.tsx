import { useEffect, useMemo, useState } from "react";
import type { ArtifactReport, Asset, Estimate, Policy, ProviderInfo, Repair, RenderOptions, ViewName } from "../api";
import { VIEWS, canOpenFolder, openFolder } from "../api";
import { Modal } from "../Help";
import { ViewCard } from "../ViewCard";
import { Chip, EstimateModal, Field, FileButton, Notice, Seg, msg, statusTone, useApp, useBlob } from "../ui";

const POLICIES: { value: Policy; label: string }[] = [
  { value: "none", label: "None (asset only)" }, { value: "building_foundation", label: "Building foundation" },
  { value: "local_plinth", label: "Local plinth" }, { value: "scene_context", label: "Scene context" }, { value: "full_master_base", label: "Full master base" },
];
const DEF: Record<string, Record<string, any>> = {
  building: { width: 4, depth: 3, height: 5.6, roof: "flat", roof_pitch_deg: 25, awning: true, tank: false, color: "#8A7F72" },
  vehicle: { width: 1.9, depth: 4.4, height: 1.5, color: "#E6A52D" }, character: { width: 0.6, depth: 0.35, height: 1.75, color: "#C9A27E" },
  animal: { width: 0.4, depth: 0.9, height: 0.7, color: "#8B6B4A" }, prop: { width: 0.8, depth: 0.8, height: 0.8, color: "#9A7B4F" },
  furniture: { width: 1, depth: 0.6, height: 0.9, color: "#8A5A3A" }, sign: { width: 0.9, depth: 0.1, height: 2.6, color: "#1445B0" },
  light: { width: 0.3, depth: 0.3, height: 4.5, color: "#3A3A3A" }, vegetation: { width: 1.6, depth: 1.6, height: 3, color: "#3E7A3A" },
  structure: { width: 3, depth: 2, height: 2.5, color: "#7A7A7A" }, background: { width: 6, depth: 0.2, height: 4, color: "#808080" },
  effect: { width: 1, depth: 1, height: 1, color: "#808080" }, other: { width: 1, depth: 1, height: 1, color: "#808080" },
};
const BASE_DEF = { width: 12, depth: 8, thickness: 0.35, bevel: 0.04, border: 0.08, lanes: 2, material: "dark_stone" };
const MATERIALS = ["dark_stone", "black_lacquer_wood", "concrete", "aged_stone", "dark_metal", "wood_tabletop"];
const TOGGLES: [string, string][] = [
  ["neutralize_cast", "Neutralize unwanted global yellow/orange cast"], ["preserve_warm_lights", "Preserve intentional amber lamps and neon glow"],
  ["reduce_noise", "Reduce speckle/chroma noise"], ["avoid_waxy_skin", "Avoid waxy/plastic skin (characters)"], ["preserve_texture", "Preserve texture microdetail"],
  ["check_shape", "Check shape distortion and inconsistent parts"], ["check_text", "Check signs/text fidelity"], ["check_background", "Check white background and shadow spill"],
  ["check_consistency", "Check consistency across five views"], ["show_diff", "Show before/after with diff heatmap and mask"],
];

function Num(props: { label: string; v: number; set: (n: number) => void; min?: number; max?: number; step?: number }) {
  return <Field label={props.label}><input type="number" min={props.min} max={props.max} step={props.step ?? 0.1} value={props.v}
    onChange={(e) => props.set(Number(e.target.value))} /></Field>;
}

function ModelTab({ asset }: { asset: Asset }) {
  const { api, run, busy, setError, refresh, setNotice } = useApp();
  const isBase = asset.fixture === "master_base";
  const isBar = asset.fixture === "bar";
  const base = DEF[asset.category] ?? DEF.other;
  const [p, setP] = useState<Record<string, any>>({});
  const [yaw, setYaw] = useState(0);
  const [th, setTh] = useState("");
  const [gen, setGen] = useState(false);
  const [mp, setMp] = useState<ProviderInfo[]>([]);
  const [gp, setGp] = useState({ provider: "mock_mesh", model: "mock_mesh:box-v1", texture: true, pbr: false, poly: "" });
  const [est, setEst] = useState<Estimate | null>(null);
  const mesh = (asset.params as any)?._mesh;
  useEffect(() => {
    const saved = { ...(asset.params as Record<string, any>) }; delete saved._mesh;
    setP(isBase ? { ...BASE_DEF, ...saved } : isBar ? { plinth_margin: 0.5, plinth_height: 0.2, ...saved } : { ...base, ...saved });
    setYaw(mesh?.yaw_deg ?? 0); setTh(mesh?.target_height ? String(mesh.target_height) : "");
  }, [asset.id, asset.model_revision, asset.mesh_status]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api.aiProviders().then((r) => setMp(r.mesh)).catch(() => undefined); }, [api]);
  const set = (k: string, v: any) => setP({ ...p, [k]: v });
  const build = () => void run(asset.mesh_status === "NOT_CREATED" ? "Building proxy" : "Rebuilding proxy", () => api.buildProxy(asset.id, p), () => refresh());
  const startGen = async () => {
    try {
      const options: Record<string, unknown> = { texture: gp.texture, pbr: gp.pbr };
      if (gp.poly) options.target_polycount = Number(gp.poly);
      const e = await api.meshEstimate(asset.id, { provider_id: gp.provider, model_id: gp.model, options });
      if (!e.requires_approval) await doGen(e, true); else setEst(e);
      setGen(false);
    } catch (x) { setError(msg(x)); }
  };
  const doGen = async (e: Estimate, ok: boolean) => {
    setEst(null);
    await run("Generating 3D model", () => api.meshGenerate(asset.id, e.estimate_id, ok), async (j) => { await refresh(); if (j.state === "SUCCEEDED") setNotice("Model downloaded. Review its orientation, then approve it."); });
  };
  const flow = async (fn: () => Promise<unknown>, ok?: string) => { try { await fn(); await refresh(); if (ok) setNotice(ok); } catch (e) { setError(msg(e)); } };

  return (
    <>
      <h2>3D model</h2>
      <p><Chip tone={statusTone(asset.mesh_status)}>{asset.mesh_status.replace(/_/g, " ").toLowerCase()}</Chip> {asset.model_revision && <small className="hint">revision {asset.model_revision.slice(0, 8)}</small>}</p>
      {isBase ? (
        <>
          <p className="hint">The master diorama base is its own asset. It is never merged into a building.</p>
          <div className="two-col"><Num label="Width (m)" v={p.width} set={(n) => set("width", n)} min={1} max={60} /><Num label="Depth (m)" v={p.depth} set={(n) => set("depth", n)} min={1} max={60} /></div>
          <div className="two-col"><Num label="Thickness (m)" v={p.thickness} set={(n) => set("thickness", n)} min={0.05} max={3} step={0.05} /><Num label="Bevel (m)" v={p.bevel} set={(n) => set("bevel", n)} min={0} max={0.5} step={0.01} /></div>
          <div className="two-col"><Num label="Border (m)" v={p.border} set={(n) => set("border", n)} min={0} max={1} step={0.02} /><Num label="Road lanes" v={p.lanes} set={(n) => set("lanes", Math.round(n))} min={0} max={6} step={1} /></div>
          <Field label="Material"><select value={p.material} onChange={(e) => set("material", e.target.value)}>{MATERIALS.map((m) => <option key={m} value={m}>{m.replace(/_/g, " ")}</option>)}</select></Field>
        </>
      ) : isBar ? (
        <div className="two-col"><Num label="Plinth margin (m)" v={p.plinth_margin} set={(n) => set("plinth_margin", n)} min={0} max={5} /><Num label="Plinth height (m)" v={p.plinth_height} set={(n) => set("plinth_height", n)} min={0.02} max={1} step={0.05} /></div>
      ) : asset.fixture === "custom" ? (
        <>
          <div className="two-col"><Num label="Width (m)" v={p.width} set={(n) => set("width", n)} min={0.1} max={40} /><Num label="Depth (m)" v={p.depth} set={(n) => set("depth", n)} min={0.1} max={40} /></div>
          <div className="two-col"><Num label="Height (m)" v={p.height} set={(n) => set("height", n)} min={0.1} max={40} /><Field label="Colour"><input type="color" value={p.color ?? "#808080"} onChange={(e) => set("color", e.target.value)} /></Field></div>
          {asset.category === "building" && (
            <>
              <div className="two-col"><Field label="Roof"><select value={p.roof} onChange={(e) => set("roof", e.target.value)}><option value="flat">Flat</option><option value="gable">Gable</option><option value="shed">Shed</option></select></Field>
                <Num label="Roof pitch (°)" v={p.roof_pitch_deg} set={(n) => set("roof_pitch_deg", n)} min={0} max={60} step={1} /></div>
              <label className="check"><input type="checkbox" checked={!!p.awning} onChange={(e) => set("awning", e.target.checked)} />Awning over the door</label>
              <label className="check"><input type="checkbox" checked={!!p.tank} onChange={(e) => set("tank", e.target.checked)} />Roof water tank</label>
            </>
          )}
        </>
      ) : <p className="hint">This is a built-in test fixture with a fixed shape.</p>}
      <button className="btn primary" disabled={busy || asset.fixture === "calibration_cube" || asset.fixture === "chart" && false} onClick={build}>
        {asset.mesh_status === "NOT_CREATED" ? "Build Proxy" : "Rebuild Proxy"}</button>
      <p className="hint">Changing the model marks existing views stale. Re-render them afterwards.</p>

      {!isBase && (
        <>
          <h2>Use your own model</h2>
          <FileButton label="Import .glb…" accept=".glb,model/gltf-binary" small onFile={(f) => void flow(() => api.importMesh(asset.id, f), "Imported. Review the orientation, then approve.")} disabled={busy} />
          <p className="hint">Imported files are checked before use. Everything must be embedded in the .glb. Orientation and pivot are always reviewed first.</p>
          {mesh && (
            <div className="subpanel">
              <h3>Orientation</h3>
              <div className="two-col">
                <Field label="Rotate"><select value={yaw} onChange={(e) => setYaw(+e.target.value)}>{[0, 90, 180, 270].map((d) => <option key={d} value={d}>{d}° (front faces {d === 0 ? "-Y" : d === 90 ? "+X" : d === 180 ? "+Y" : "-X"})</option>)}</select></Field>
                <Field label="Height (m, optional)"><input inputMode="decimal" value={th} onChange={(e) => setTh(e.target.value.replace(/[^\d.]/g, ""))} placeholder="as imported" /></Field>
              </div>
              <div className="actions left">
                <button className="btn sm" disabled={busy} onClick={() => void flow(() => api.meshOrientation(asset.id, yaw, th ? Number(th) : null))}>Apply</button>
                <button className="btn sm primary" disabled={busy || asset.mesh_status !== "IMPORTED_PENDING_REVIEW"} onClick={() => void flow(() => api.approveMesh(asset.id), "Mesh approved. You can render now.")}>Approve mesh</button>
              </div>
              <p className="hint">The base of the model is placed on the ground and centred. Check that the front faces the front view.</p>
              {mesh.provenance && <p className="hint">Made by {mesh.provenance.provider} (task {String(mesh.provenance.task_id).slice(0, 10)}). Back and sides are guessed.</p>}
            </div>
          )}
          {asset.has_crop && <button className="btn sm" disabled={busy} onClick={() => setGen(true)}>Generate 3D with AI…</button>}
        </>
      )}

      {gen && (
        <Modal title="Generate 3D with AI" onClose={() => setGen(false)}>
          <p className="hint">Sends this item's cropped source image to the provider you pick. The unseen sides are guessed.</p>
          <Field label="Provider"><select value={gp.provider + "|" + gp.model} onChange={(e) => { const [a, b] = e.target.value.split("|"); setGp({ ...gp, provider: a, model: b }); }}>
            {mp.flatMap((pr) => pr.models.map((m) => <option key={m.id} value={pr.id + "|" + m.id} disabled={["disabled", "deprecated"].includes(m.status) || (pr.needs_key && !pr.configured)}>
              {m.label} · {m.status}{pr.needs_key && !pr.configured ? " · connect key" : ""}</option>))}</select></Field>
          <label className="check"><input type="checkbox" checked={gp.texture} onChange={(e) => setGp({ ...gp, texture: e.target.checked })} />Textured</label>
          <label className="check"><input type="checkbox" checked={gp.pbr} disabled={!gp.texture} onChange={(e) => setGp({ ...gp, pbr: e.target.checked })} />PBR materials</label>
          <Field label="Target polygon count (optional)"><input inputMode="numeric" value={gp.poly} onChange={(e) => setGp({ ...gp, poly: e.target.value.replace(/\D/g, "") })} /></Field>
          <div className="modal-f"><button className="btn" onClick={() => setGen(false)}>Cancel</button><button className="btn primary" onClick={() => void startGen()}>Continue</button></div>
        </Modal>
      )}
      {est && <EstimateModal title="Review before generating a model" estimate={est} confirmLabel="Confirm and generate" onCancel={() => setEst(null)} onConfirm={(ok) => void doGen(est, ok)} />}
    </>
  );
}

function RepairView({ asset, repair, onDone }: { asset: Asset; repair: Repair & { files?: string[] }; onDone: () => void }) {
  const { api, setError, refresh, setNotice } = useApp();
  const [tab, setTab] = useState<"before" | "after" | "diff" | "mask">("after");
  const [note, setNote] = useState("");
  const url = useBlob(() => api.repairUrl(asset.id, repair.id, tab), [api, asset.id, repair.id, tab]);
  const b = repair.metrics?.before?.cast ?? repair.metrics?.before?.noise, a = repair.metrics?.after?.cast ?? repair.metrics?.after?.noise;
  const metric = repair.kind === "cast" ? [b?.magnitude, a?.magnitude, "cast (Lab)"] : [b?.sigma, a?.sigma, "noise sigma"];
  return (
    <Modal title={`Preview: ${repair.kind === "cast" ? "neutralize colour cast" : "reduce flat-area noise"} (${repair.view})`} onClose={onDone} wide>
      <Seg label="Image" value={tab} onChange={setTab} options={[{ value: "before", label: "Before" }, { value: "after", label: "After" }, { value: "diff", label: "Diff heatmap" }, { value: "mask", label: "Mask" }]} />
      <div className="repair-img">{url ? <img src={url} alt={`${tab} image`} /> : <span className="hint">Loading…</span>}</div>
      <p>{metric[2]}: {metric[0] != null ? Number(metric[0]).toFixed(2) : "n/a"} → {metric[1] != null ? Number(metric[1]).toFixed(2) : "n/a"}.{" "}
        {repair.kind === "cast" && (repair.metrics.protected_untouched ? `${repair.metrics.protected_pixels} protected lamp/neon pixels were left untouched.` : "Protected pixels changed.")}</p>
      <p className="hint">Nothing is applied until you accept. The original is always kept and can be restored.</p>
      <Field label="Note (kept in the repair log)"><input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} /></Field>
      <div className="modal-f">
        <button className="btn" onClick={async () => { try { await api.rejectRepair(asset.id, repair.id); onDone(); } catch (e) { setError(msg(e)); } }}>Discard</button>
        <button className="btn primary" onClick={async () => { try { await api.acceptRepair(asset.id, repair.id, note); await refresh(); setNotice("Repair applied. The view needs your review again."); onDone(); } catch (e) { setError(msg(e)); } }}>Accept repair</button>
      </div>
    </Modal>
  );
}

function QualityTab({ asset }: { asset: Asset }) {
  const { api, setError, refresh, setNotice } = useApp();
  const [settings, setSettings] = useState<Record<string, any>>({ mode: "studio" });
  const [reports, setReports] = useState<Record<string, ArtifactReport>>({});
  const [cons, setCons] = useState<any[]>([]);
  const [prev, setPrev] = useState<(Repair & { files?: string[] }) | null>(null);
  const [busy, setBusy] = useState(false);
  const rendered = VIEWS.filter((v) => asset.views[v].path);
  const analyze = async () => {
    setBusy(true); setError(null);
    try { const r = await api.analyzeAsset(asset.id, settings); setReports(r.views); setCons(r.consistency); }
    catch (e) { setError(msg(e)); } finally { setBusy(false); }
  };
  const preview = async (v: ViewName, kind: "cast" | "noise") => {
    try { setPrev(await api.previewRepair(asset.id, v, kind, 1)); } catch (e) { setError(msg(e)); }
  };
  const revert = async (v: ViewName) => { try { await api.revertRepair(asset.id, v); await refresh(); await analyze(); setNotice("Restored the version you had before repairs."); } catch (e) { setError(msg(e)); } };
  return (
    <>
      <h2>Quality checks</h2>
      <p className="hint">These checks are heuristics, not guarantees. Warm lamps and neon are protected, texture is not treated as noise, and skin is judged by a person.</p>
      <Seg label="Lighting intent" value={settings.mode} onChange={(m) => setSettings({ ...settings, mode: m })}
        options={[{ value: "studio", label: "Neutral studio", title: "Treat a global colour cast as a defect" }, { value: "scene_grade", label: "Warm scene grade", title: "A deliberate warm look is left alone" }]} />
      <details><summary>Checks</summary>
        {TOGGLES.map(([k, l]) => <label className="check" key={k}><input type="checkbox" checked={settings[k] !== false} onChange={(e) => setSettings({ ...settings, [k]: e.target.checked })} />{l}</label>)}
      </details>
      <button className="btn primary" disabled={busy || rendered.length === 0} onClick={() => void analyze()}>Analyze rendered views</button>
      {rendered.length === 0 && <p className="hint">Render the views first.</p>}
      {cons.map((f) => <Notice key={f.code} tone="bad">{f.detail}</Notice>)}
      {rendered.map((v) => {
        const r = reports[v];
        const hasCast = r?.findings.some((f) => f.code === "COLOR_CAST"), hasNoise = r?.findings.some((f) => f.code === "NOISE");
        return (
          <div className="subpanel" key={v}>
            <h3>{v.toUpperCase()}</h3>
            {!r ? <p className="hint">Not analyzed yet.</p> : (
              <>
                {r.findings.filter((f) => f.severity !== "info").length === 0 && <Chip tone="ok">No issues found by the automatic checks</Chip>}
                {r.findings.filter((f) => f.severity !== "info").map((f) => (
                  <div key={f.code} className="finding"><Chip tone={f.severity === "critical" ? "bad" : "warn"}>{f.severity}</Chip> <span>{f.detail}</span>
                    {f.remediation?.map((x) => <small key={x} className="hint">• {x}</small>)}</div>
                ))}
                <div className="actions left">
                  {hasCast && <button className="btn sm" onClick={() => void preview(v, "cast")}>Preview colour fix</button>}
                  {hasNoise && <button className="btn sm" onClick={() => void preview(v, "noise")}>Preview noise fix</button>}
                  {((asset.views[v].qa?.repairs as unknown[]) ?? []).length > 0 && <button className="btn sm" onClick={() => void revert(v)}>Restore original</button>}
                </div>
              </>
            )}
          </div>
        );
      })}
      {prev && <RepairView asset={asset} repair={prev} onDone={() => { setPrev(null); void analyze(); }} />}
    </>
  );
}

export function Studio() {
  const { api, project, assets, selectedId, setSelectedId, blender, run, busy, setError, refresh, goto } = useApp();
  const [tab, setTab] = useState<"model" | "render" | "quality">("model");
  const [elev, setElev] = useState<Policy>("building_foundation");
  const [persp, setPersp] = useState<Policy>("local_plinth");
  const [opts, setOpts] = useState({ resolution: 1024, engine: "CYCLES" as RenderOptions["engine"], samples: 64, quadrant: "front_left" as RenderOptions["perspective_quadrant"], elevation: 30, focal: 50 });
  const [exportFolder, setExportFolder] = useState<string | null>(null);
  const [exportMsg, setExportMsg] = useState("");

  const list = useMemo(() => assets.filter((a) => a.fixture !== "custom" || a.goal === "3D_PROXY" || a.goal === "3D_RECONSTRUCTION"), [assets]);
  const asset = list.find((a) => a.id === selectedId) ?? list[0] ?? null;
  useEffect(() => { if (asset && asset.id !== selectedId) setSelectedId(asset.id); }, [asset?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!asset) return;
    const bld = asset.category === "building";
    setElev(bld ? "building_foundation" : "none"); setPersp(bld ? "local_plinth" : "none");
  }, [asset?.id, asset?.category]);
  const topUrl = useBlob(asset?.fixture === "master_base" && asset.views.front.path ? () => api.extraUrl(asset.id, "top") : null, [api, asset?.id, asset?.views.front.sha256]);
  if (!project) return <div className="page"><Notice>Open or create a project first.</Notice></div>;
  if (!asset) {
    return (
      <div className="page">
        <Notice>No 3D assets yet. Mark an item as "Mesh + five views" in Decompose, or add the master base.</Notice>
        <div className="actions left"><button className="btn" onClick={() => goto("decompose")}>Decompose</button>
          {project.scene_mode === "DIORAMA" && <button className="btn" onClick={async () => { try { const a = await api.addFixture(project.id, "master_base"); await refresh(); setSelectedId(a.id); } catch (e) { setError(msg(e)); } }}>Add diorama base</button>}</div>
      </div>
    );
  }
  const hasMesh = ["PROXY_APPROVED", "MESH_APPROVED"].includes(asset.mesh_status);
  const anyStale = VIEWS.some((v) => asset.views[v].stale && !asset.views[v].qa?.findings?.some((f) => f.code === "RENDER_PENDING"));
  const mixed = elev !== persp;
  const ro = (views?: ViewName[]): RenderOptions => ({
    resolution: opts.resolution, engine: opts.engine, samples: opts.samples,
    base_policies: { front: elev, back: elev, left: elev, right: elev, perspective: persp },
    perspective_quadrant: opts.quadrant, perspective_elevation_degrees: opts.elevation, perspective_focal_mm: opts.focal, ...(views ? { views } : {}),
  });
  const render = (views?: ViewName[]) => void run(views ? `Rendering ${views[0]}` : "Rendering five views", () => api.renderFive(asset.id, ro(views)), () => refresh());
  const exportNow = () => void run("Exporting ZIP", () => api.exportProject(project.id), (j) => { setExportFolder(j.result?.folder ?? null); setExportMsg(j.result?.status ?? ""); });

  return (
    <div className="work two">
      <section className="work-main" aria-label="Five views">
        <div className="work-head">
          <div>
            <h1>{asset.name} <small className="muted">{asset.code}</small></h1>
            <p className="hint">Four orthographic camera renders plus one perspective render, all from the same 3D object.</p>
          </div>
          <div className="actions">
            <select aria-label="Asset" value={asset.id} onChange={(e) => setSelectedId(e.target.value)}>{list.map((a) => <option key={a.id} value={a.id}>{a.code} · {a.name}</option>)}</select>
            <button className={`btn ${hasMesh && !anyStale ? "" : "primary"}`} disabled={busy || !blender?.found} onClick={() => setTab("model")}>{hasMesh ? "Edit model" : "Build model"}</button>
            <button className={`btn hero ${hasMesh ? "primary" : ""}`} disabled={busy || !hasMesh || !blender?.found}
              title={hasMesh ? "Render all five views" : "Cannot produce verified side views without a 3D model. Build a proxy or import a mesh."}
              onClick={() => render()}>Render 5 Views</button>
            <button className="btn" disabled={busy} onClick={exportNow}>Export ZIP</button>
            <button className="btn" disabled={!exportFolder || !canOpenFolder()} title={canOpenFolder() ? "Open the export folder" : "Available in the desktop app"} onClick={() => exportFolder && void openFolder(exportFolder)}>Open Folder</button>
          </div>
        </div>
        {!blender?.found && <Notice tone="bad">Blender was not found. Set its path in Settings.</Notice>}
        {!hasMesh && blender?.found && <Notice tone="warn">{asset.mesh_status === "IMPORTED_PENDING_REVIEW" ? "Review the imported model's orientation and approve it in the Model tab." : "Cannot produce verified side views without a 3D model. Build a proxy or import a mesh."}</Notice>}
        {anyStale && <Notice tone="warn">The model changed after these views were rendered. Re-render before approving or exporting.</Notice>}
        {exportMsg && <Notice>Export {exportMsg}. {exportMsg !== "COMPLETE" && "Every asset needs all five views approved for a complete export."}</Notice>}
        {asset.category === "building" && <Notice>The back and sides of this building are not visible in your source. Review inferred geometry before approval.</Notice>}
        <div className="views" role="group" aria-label="Five views">
          {VIEWS.map((v) => (
            <ViewCard key={v} asset={asset} view={v} api={api} disabled={busy} canRender={hasMesh && !!blender?.found} onRerender={() => render([v])}
              onReview={async (s) => { try { await api.review(asset.id, v, s); await refresh(); } catch (e) { setError(msg(e)); } }} />
          ))}
        </div>
        {asset.fixture === "master_base" && (
          <article className="card top-card"><div className="card-h"><b>TOP</b><span className="badge">ORTHO</span><span className="chip">extra</span></div>
            <div className="frame">{topUrl ? <img src={topUrl} alt="Top view of the master base" /> : <div className="empty">Rendered together with the five views</div>}</div></article>
        )}
      </section>

      <aside className="panel side" aria-label="Studio settings">
        <Seg label="Panel" value={tab} onChange={setTab} options={[{ value: "model", label: "Model" }, { value: "render", label: "Render" }, { value: "quality", label: "Quality" }]} />
        {tab === "model" && <ModelTab asset={asset} />}
        {tab === "quality" && <QualityTab asset={asset} />}
        {tab === "render" && (
          <>
            <h2>Render settings</h2>
            <Field label="Elevation base policy (front/back/left/right)"><select value={elev} onChange={(e) => setElev(e.target.value as Policy)}>{POLICIES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}</select></Field>
            <Field label="Perspective base policy"><select value={persp} onChange={(e) => setPersp(e.target.value as Policy)}>{POLICIES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}</select></Field>
            {mixed && <p className="hint">Elevations and perspective use different bases. Fine for presentation; use one policy for dimension-matched 3D references.</p>}
            {project.scene_mode === "REGULAR_IMAGE" && project.base_mode === "NONE" && <p className="hint">This is a regular image project, so no base is added. Change the project base in Decompose to opt in.</p>}
            <div className="two-col">
              <Field label="Resolution"><select value={opts.resolution} onChange={(e) => setOpts({ ...opts, resolution: +e.target.value })}>{[512, 1024, 1536, 2048].map((r) => <option key={r} value={r}>{r} × {r}</option>)}</select></Field>
              <Field label="Engine"><select value={opts.engine} onChange={(e) => setOpts({ ...opts, engine: e.target.value as RenderOptions["engine"] })}><option value="CYCLES">Cycles (final)</option><option value="WORKBENCH">Workbench (fast draft)</option></select></Field>
            </div>
            <Field label="Perspective corner"><select value={opts.quadrant} onChange={(e) => setOpts({ ...opts, quadrant: e.target.value as typeof opts.quadrant })}><option value="front_left">Front-left</option><option value="front_right">Front-right</option><option value="rear_left">Rear-left</option><option value="rear_right">Rear-right</option></select></Field>
            <div className="two-col">
              <Field label="Elevation (deg)"><input type="number" min={0} max={89} value={opts.elevation} onChange={(e) => setOpts({ ...opts, elevation: Math.min(89, Math.max(0, +e.target.value)) })} /></Field>
              <Field label="Focal length (mm)"><input type="number" min={10} max={300} value={opts.focal} onChange={(e) => setOpts({ ...opts, focal: Math.min(300, Math.max(10, +e.target.value)) })} /></Field>
            </div>
            <p className="hint">Cycles takes about 30 seconds for all five views on this computer. Workbench takes a few.</p>
          </>
        )}
      </aside>
    </div>
  );
}
