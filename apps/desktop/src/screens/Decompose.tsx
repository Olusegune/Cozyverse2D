import { useCallback, useEffect, useMemo, useState } from "react";
import type { Asset, Estimate, OutputGoal, Source, TreeNode } from "../api";
import { ImageStage, StageBox } from "../ImageStage";
import { Modal } from "../Help";
import { Chip, EstimateModal, Field, Notice, msg, statusTone, useApp, useBlob } from "../ui";

const CATS = ["building", "vehicle", "character", "animal", "prop", "sign", "light", "vegetation", "furniture", "structure", "background", "effect", "other"];
const VIS = [["fully_visible", "Fully visible"], ["partially_visible", "Partly visible"], ["front_and_partial_side", "Front and part of a side"], ["mostly_occluded", "Mostly hidden"], ["unseen_inferred", "Not visible (inferred)"]];
const GOALS: [OutputGoal, string][] = [["2D_LAYER_EXPORT", "2D layers"], ["REFERENCE_ONLY", "Reference only (unverified 2D)"], ["3D_PROXY", "3D proxy"], ["3D_RECONSTRUCTION", "Mesh + five views"]];

function flat(n: TreeNode[]): TreeNode[] { return n.flatMap((x) => [x, ...flat(x.children)]); }

function Row(props: { n: TreeNode; depth: number; sel: string | null; picked: Set<string>; onSel: (id: string) => void; onPick: (id: string) => void }) {
  const { n } = props;
  return (
    <>
      <li className={`irow ${n.id === props.sel ? "on" : ""}`} style={{ paddingLeft: 8 + props.depth * 16 }}>
        {n.fixture === "custom" && <input type="checkbox" aria-label={`Select ${n.name} for merge`} checked={props.picked.has(n.id)} onChange={() => props.onPick(n.id)} />}
        <button className="irow-b" onClick={() => props.onSel(n.id)}>
          <span className="iname">{n.name}</span>
          <small>{n.code}{n.count > 1 ? ` ×${n.count}` : ""}</small>
        </button>
        {n.status !== "confirmed" && <Chip tone={statusTone(n.status)}>{n.status}</Chip>}
      </li>
      {n.children.map((c) => <Row key={c.id} {...props} n={c} depth={props.depth + 1} />)}
    </>
  );
}

export function Decompose() {
  const { api, project, setProject, goto, run, busy, setError, setNotice, refresh, setSelectedId } = useApp();
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [src, setSrc] = useState<Source | null>(null);
  const [mode, setMode] = useState<"select" | "add" | "instance">("select");
  const [draft, setDraft] = useState<number[] | null>(null);
  const [nu, setNu] = useState({ name: "", category: "prop", parent_id: "" });
  const [form, setForm] = useState<Partial<Asset>>({});
  const [audit, setAudit] = useState<Awaited<ReturnType<typeof api.audit>> | null>(null);
  const [detect, setDetect] = useState(false);
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [vision, setVision] = useState<{ id: string; label: string; needs_key: boolean; needs_model: boolean; configured: boolean }[]>([]);
  const [dp, setDp] = useState({ provider: "mock_vision", model: "", tiles: 0 });
  const [minConf, setMinConf] = useState(0.8);
  const [confirmDel, setConfirmDel] = useState(false);
  const [showZones, setShowZones] = useState(false);

  const reload = useCallback(async () => {
    if (!project) return;
    const [t, srcs] = await Promise.all([api.tree(project.id), api.sources(project.id)]);
    setTree(t);
    setSrc(srcs.find((s) => s.id === project.approved_source_id) ?? null);
    await refresh();
  }, [api, project?.id, project?.approved_source_id, refresh]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { void reload().catch((e) => setError(msg(e))); }, [reload, setError]);
  useEffect(() => { api.aiProviders().then((r) => setVision(r.vision)).catch(() => undefined); }, [api]);

  const items = useMemo(() => flat(tree), [tree]);
  const cur = items.find((i) => i.id === sel) ?? null;
  useEffect(() => { setForm(cur ? { name: cur.name, category: cur.category, count: cur.count, visibility: cur.visibility, confidence: cur.confidence, status: cur.status, goal: cur.goal, notes: cur.notes, parent_id: cur.parent_id, kind: cur.kind } : {}); setConfirmDel(false); }, [cur?.id, cur?.name, cur?.status, cur?.goal, cur?.count]); // eslint-disable-line react-hooks/exhaustive-deps
  const srcUrl = useBlob(src ? () => api.sourceUrl(src.id) : null, [api, src?.id]);
  const cropUrl = useBlob(cur?.has_crop ? () => api.cropUrl(cur.id) : null, [api, cur?.id, cur?.bbox?.join(",")]);

  if (!project) return <div className="page"><Notice>Open or create a project first.</Notice></div>;
  if (!src) {
    return (
      <div className="page">
        <Notice tone="warn">Approve a source image before breaking it down. Generating or importing never starts a breakdown on its own.</Notice>
        <div className="actions left"><button className="btn" onClick={() => goto("create")}>Create</button><button className="btn" onClick={() => goto("editor")}>Editor</button></div>
      </div>
    );
  }

  const withBox = items.filter((i) => i.bbox);
  const boxes: StageBox[] = [
    ...withBox.map((i) => ({ id: i.id, bbox: i.bbox!, tone: (i.status === "confirmed" ? "ok" : i.status === "disputed" ? "bad" : "warn") as StageBox["tone"] })),
    ...(showZones && audit ? audit.empty_zones.map((z, k) => ({ id: `z${k}`, bbox: z.zone, tone: "dim" as const, dashed: true })) : []),
  ];
  const save = async () => {
    if (!cur) return;
    try { await api.patchItem(cur.id, { ...form, parent_id: form.parent_id || undefined }); await reload(); setNotice("Saved."); } catch (e) { setError(msg(e)); }
  };
  const create = async () => {
    if (!draft) return;
    try {
      const a = await api.createItem(project.id, { name: nu.name.trim() || "New item", category: nu.category, bbox: draft, ...(nu.parent_id ? { parent_id: nu.parent_id } : {}),
        goal: project.output_goal === "3D_RECONSTRUCTION" || project.output_goal === "3D_PROXY" ? project.output_goal : project.output_goal === "REFERENCE_ONLY" ? "REFERENCE_ONLY" : "2D_LAYER_EXPORT" });
      setDraft(null); setNu({ ...nu, name: "" }); setMode("select"); setSel(a.id); await reload();
    } catch (e) { setError(msg(e)); }
  };
  const addInstance = async (b: number[]) => {
    if (!cur) return;
    try { await api.addInstance(cur.id, b); setMode("select"); await reload(); setNotice(`Instance added to ${cur.name}.`); } catch (e) { setError(msg(e)); }
  };
  const del = async () => { if (!cur) return; try { await api.deleteItem(cur.id); setSel(null); await reload(); } catch (e) { setError(msg(e)); } };
  const merge = async () => {
    const ids = [...picked];
    if (ids.length < 2) return;
    try { await api.mergeItems(project.id, ids, ids[0]); setPicked(new Set()); setSel(ids[0]); await reload(); setNotice("Merged. The other boxes were kept as instances."); } catch (e) { setError(msg(e)); }
  };
  const runDetect = async () => {
    try { const e = await api.detectEstimate(project.id, dp); if (!e.requires_approval) await startDetect(e, true); else setEstimate(e); setDetect(false); }
    catch (e) { setError(msg(e)); }
  };
  const startDetect = async (e: Estimate, ok: boolean) => {
    setEstimate(null);
    await run("Detecting objects", () => api.detect(project.id, e.estimate_id, ok), async (job) => { await reload(); if (job.state === "SUCCEEDED") setNotice(job.result?.message ?? "Done. Review every proposal."); });
  };
  const confirmAll = async () => {
    try { const r = await api.confirmItems(project.id, null, minConf); await reload(); setNotice(`${r.confirmed} proposals confirmed.`); } catch (e) { setError(msg(e)); }
  };
  const goStudio = () => { if (!cur) return; setSelectedId(cur.id); goto("studio"); };
  const unconfirmed = items.filter((i) => i.status === "unconfirmed").length;
  const parents = items.filter((i) => i.id !== cur?.id && i.fixture === "custom");

  return (
    <div className="work two decomp">
      <section className="work-main" aria-label="Source with detections">
        <div className="work-head">
          <div><h1>Decompose</h1><p className="hint">{project.name} · {project.scene_mode === "DIORAMA" ? "Diorama" : "Regular image"} · source {src.id.slice(2, 8)} {src.approved && <Chip tone="ok">approved</Chip>}</p></div>
          <div className="actions">
            <button className={`btn ${mode === "add" ? "primary" : ""}`} onClick={() => { setMode(mode === "add" ? "select" : "add"); setDraft(null); }}>{mode === "add" ? "Cancel drawing" : "Add item"}</button>
            <button className="btn" disabled={busy} onClick={() => setDetect(true)}>Detect objects…</button>
            <button className={`btn ${showZones ? "on" : ""}`} onClick={async () => { const a = await api.audit(project.id); setAudit(a); setShowZones(!showZones); }}>Audit</button>
          </div>
        </div>
        {mode === "add" && <Notice>Drag a rectangle around the object on the image.</Notice>}
        {mode === "instance" && <Notice>Drag a rectangle around another copy of {cur?.name}.</Notice>}
        <ImageStage src={srcUrl} width={src.width} height={src.height} alt="Approved source image" boxes={boxes} selectedId={sel}
          onSelect={(id) => { if (id && !id.startsWith("z")) setSel(id); }} draw={mode !== "select"} draft={draft}
          onDraw={(b) => { if (mode === "instance") void addInstance(b); else setDraft(b); }} />
        {audit && showZones && (
          <div className="panel audit" aria-label="Omission audit">
            <h2>Omission audit</h2>
            <p>{audit.item_count} items with boxes. {audit.unconfirmed.length} unconfirmed, {audit.tiny_items.length} tiny, {audit.empty_zones.length} empty zones (dashed).</p>
            <p className="hint">{audit.hint}</p>
            {audit.tiny_items.length > 0 && <p>Tiny items: {audit.tiny_items.map((t) => t.name).join(", ")}</p>}
          </div>
        )}
      </section>

      <aside className="panel side" aria-label="Inventory">
        <div className="panel-h"><h2>Inventory ({items.filter((i) => i.fixture === "custom").length})</h2>
          <button className="btn sm" disabled={picked.size < 2} onClick={() => void merge()} title="Merge the checked items into the first one">Merge {picked.size > 1 ? picked.size : ""}</button></div>
        {unconfirmed > 0 && (
          <div className="confirm-bar">
            <Chip tone="warn">{unconfirmed} unconfirmed</Chip>
            <label className="hint">confidence ≥ <input type="number" min={0} max={1} step={0.05} value={minConf} onChange={(e) => setMinConf(+e.target.value)} style={{ width: 64 }} /></label>
            <button className="btn sm" onClick={() => void confirmAll()}>Confirm</button>
          </div>
        )}
        <ul className="itree">
          {tree.filter((n) => n.fixture === "custom" || n.children.length).map((n) => <Row key={n.id} n={n} depth={0} sel={sel} picked={picked} onSel={setSel} onPick={(id) => { const s = new Set(picked); s.has(id) ? s.delete(id) : s.add(id); setPicked(s); }} />)}
          {items.filter((i) => i.fixture === "custom").length === 0 && <li className="hint pad">No items yet. Add one by drawing a box, or run detection.</li>}
        </ul>

        {draft && mode === "add" && (
          <div className="subpanel">
            <h3>New item</h3>
            <Field label="Name"><input autoFocus value={nu.name} maxLength={120} onChange={(e) => setNu({ ...nu, name: e.target.value })} placeholder="e.g. Neon BAR" /></Field>
            <div className="two-col">
              <Field label="Category"><select value={nu.category} onChange={(e) => setNu({ ...nu, category: e.target.value })}>{CATS.map((c) => <option key={c}>{c}</option>)}</select></Field>
              <Field label="Belongs to"><select value={nu.parent_id} onChange={(e) => setNu({ ...nu, parent_id: e.target.value })}><option value="">Nothing (top level)</option>{items.filter((i) => i.fixture === "custom").map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}</select></Field>
            </div>
            <button className="btn primary" onClick={() => void create()}>Add to inventory</button>
          </div>
        )}

        {cur && cur.fixture === "custom" && (
          <div className="subpanel">
            <h3>{cur.code}</h3>
            {cropUrl && <img className="crop" src={cropUrl} alt={`Source crop of ${cur.name}`} />}
            <Field label="Name"><input value={form.name ?? ""} maxLength={120} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
            <div className="two-col">
              <Field label="Category"><select value={form.category ?? "other"} onChange={(e) => setForm({ ...form, category: e.target.value })}>{CATS.map((c) => <option key={c}>{c}</option>)}</select></Field>
              <Field label="Count"><input type="number" min={1} value={form.count ?? 1} onChange={(e) => setForm({ ...form, count: +e.target.value })} /></Field>
            </div>
            <Field label="What the source shows"><select value={form.visibility} onChange={(e) => setForm({ ...form, visibility: e.target.value })}>{VIS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
            <div className="two-col">
              <Field label="Review status"><select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value as Asset["status"] })}><option value="confirmed">Confirmed</option><option value="unconfirmed">Unconfirmed</option><option value="disputed">Disputed</option></select></Field>
              <Field label="Belongs to"><select value={form.parent_id ?? ""} onChange={(e) => setForm({ ...form, parent_id: e.target.value || null })}><option value="">Top level</option>{parents.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}</select></Field>
            </div>
            <Field label="Output"><select value={form.goal} onChange={(e) => setForm({ ...form, goal: e.target.value as OutputGoal })}>{GOALS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
            {form.goal === "REFERENCE_ONLY" && <p className="hint">Reference only exports the cropped 2D image marked unverified. No angle views are invented.</p>}
            <Field label="Notes"><textarea rows={2} value={form.notes ?? ""} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field>
            <div className="actions left">
              <button className="btn primary" onClick={() => void save()}>Save</button>
              <button className="btn" onClick={() => setMode("instance")}>Add another copy</button>
              {(cur.goal === "3D_PROXY" || cur.goal === "3D_RECONSTRUCTION") && <button className="btn" onClick={goStudio}>Build in 3D Studio</button>}
              {confirmDel ? <><button className="btn danger" onClick={() => void del()}>Delete for good</button><button className="btn" onClick={() => setConfirmDel(false)}>Keep</button></> : <button className="btn danger" onClick={() => setConfirmDel(true)}>Delete</button>}
            </div>
          </div>
        )}
        <div className="subpanel">
          <h3>Project output</h3>
          <Field label="What do you want from this image?">
            <select value={project.output_goal} onChange={async (e) => { try { setProject(await api.patchProject(project.id, { output_goal: e.target.value as OutputGoal })); } catch (x) { setError(msg(x)); } }}>
              <option value="2D_LAYER_EXPORT">2D layers</option><option value="REFERENCE_ONLY">3D references (unverified 2D)</option>
              <option value="3D_PROXY">3D proxies</option><option value="3D_RECONSTRUCTION">Mesh + five views</option><option value="SCENE_REASSEMBLY">Scene reassembly</option>
            </select>
          </Field>
          <p className="hint">Applies to new items. Each item can override it.</p>
          <Field label="Display base" hint={project.scene_mode === "REGULAR_IMAGE" ? "Regular images start with no base. Choose one only if you want it." : "Diorama projects use the master base by default."}>
            <select value={project.base_mode} onChange={async (e) => { try { setProject(await api.patchProject(project.id, { base_mode: e.target.value })); } catch (x) { setError(msg(x)); } }}>
              <option value="NONE">None</option><option value="FOUNDATION">Building foundation</option><option value="LOCAL_PLINTH">Local plinth</option>
              <option value="SCENE_CONTEXT">Scene context</option><option value="MASTER_DIORAMA">Master diorama base</option>
            </select>
          </Field>
        </div>
      </aside>

      {detect && (
        <Modal title="Detect objects" onClose={() => setDetect(false)}>
          <p className="hint">Detection only proposes items. Every proposal starts unconfirmed and needs your review.</p>
          <Field label="Detector"><select value={dp.provider} onChange={(e) => setDp({ ...dp, provider: e.target.value })}>
            {vision.map((v) => <option key={v.id} value={v.id} disabled={v.needs_key && !v.configured}>{v.label}{v.needs_key && !v.configured ? " · connect Google key first" : ""}</option>)}</select></Field>
          {vision.find((v) => v.id === dp.provider)?.needs_model && <Field label="Gemini model slug" hint="A model that accepts image input. See Google AI Studio."><input value={dp.model} onChange={(e) => setDp({ ...dp, model: e.target.value })} /></Field>}
          <Field label="Small-prop second pass" hint="Splits the image into tiles and looks again. More calls, more cost.">
            <select value={dp.tiles} onChange={(e) => setDp({ ...dp, tiles: +e.target.value })}><option value={0}>Off</option><option value={2}>2 × 2 tiles</option><option value={3}>3 × 3 tiles</option></select></Field>
          <div className="modal-f"><button className="btn" onClick={() => setDetect(false)}>Cancel</button><button className="btn primary" onClick={() => void runDetect()}>Continue</button></div>
        </Modal>
      )}
      {estimate && <EstimateModal title="Review before detecting" estimate={estimate} confirmLabel="Confirm and detect" onCancel={() => setEstimate(null)} onConfirm={(ok) => void startDetect(estimate, ok)} />}
    </div>
  );
}
