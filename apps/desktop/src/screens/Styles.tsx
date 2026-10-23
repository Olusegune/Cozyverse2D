import { useEffect, useMemo, useState } from "react";
import type { Style } from "../api";
import { Modal } from "../Help";
import { Chip, Field, FileButton, Notice, Seg, download, msg, useApp, useDebounced, useLocal } from "../ui";

type Tab = "all" | "favorites" | "recent" | "mine";
const DIMS = ["all", "2D", "2.5D", "3D", "photo"] as const;

function Editor({ initial, styles, onClose, onSaved }: { initial: Style | null; styles: Style[]; onClose: () => void; onSaved: () => void }) {
  const { api, setError, project } = useApp();
  const [f, setF] = useState<Record<string, any>>(() => ({ name: initial?.label ?? "", dimensionality: initial?.dimensionality ?? "2D", palette: initial?.palette ?? "", lighting: initial?.lighting ?? "",
    linework: initial?.linework ?? "", texture: initial?.texture ?? "", camera: initial?.camera ?? "", strength: initial?.strength ?? 0.7,
    negative_constraints: (initial?.negative_constraints ?? []).join(", "), blend: initial?.blend ?? [] }));
  const [sample, setSample] = useState("A rainy night street market");
  const [out, setOut] = useState("");
  const body = () => ({ ...f, negative_constraints: String(f.negative_constraints).split(",").map((s) => s.trim()).filter(Boolean) });
  const preview = async () => {
    try {
      const r = await api.compileStyle({ recipe: body(), prompt: sample, scene_mode: project?.scene_mode ?? "REGULAR_IMAGE" });
      setOut(r.prompt);
    } catch (e) { setOut(""); setError(msg(e)); }
  };
  const save = async () => {
    try { initial ? await api.updateStyle(initial.id, body()) : await api.createStyle(body()); onSaved(); onClose(); } catch (e) { setError(msg(e)); }
  };
  const blend = f.blend as { style_id: string; weight: number }[];
  return (
    <Modal title={initial ? "Edit recipe" : "New style recipe"} onClose={onClose} wide>
      <div className="grid2">
        <Field label="Name"><input value={f.name} maxLength={80} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Dimension"><select value={f.dimensionality} onChange={(e) => setF({ ...f, dimensionality: e.target.value })}><option>2D</option><option>2.5D</option><option>3D</option><option value="photo">photo</option></select></Field>
        <Field label="Palette"><input value={f.palette} onChange={(e) => setF({ ...f, palette: e.target.value })} placeholder="warm ochres and deep teal" /></Field>
        <Field label="Lighting"><input value={f.lighting} onChange={(e) => setF({ ...f, lighting: e.target.value })} placeholder="soft overcast" /></Field>
        <Field label="Linework"><input value={f.linework} onChange={(e) => setF({ ...f, linework: e.target.value })} placeholder="thin uniform outlines" /></Field>
        <Field label="Texture"><input value={f.texture} onChange={(e) => setF({ ...f, texture: e.target.value })} placeholder="visible paper grain" /></Field>
        <Field label="Camera and framing"><input value={f.camera} onChange={(e) => setF({ ...f, camera: e.target.value })} placeholder="three-quarter, slightly elevated" /></Field>
        <Field label="Avoid (comma separated)"><input value={f.negative_constraints} onChange={(e) => setF({ ...f, negative_constraints: e.target.value })} /></Field>
      </div>
      <Field label={`Strength: ${Number(f.strength).toFixed(2)}`}><input type="range" min={0} max={1} step={0.05} value={f.strength} onChange={(e) => setF({ ...f, strength: +e.target.value })} /></Field>
      <h3>Blend with other styles (up to 4)</h3>
      {blend.map((b, i) => (
        <div className="blend" key={i}>
          <select value={b.style_id} onChange={(e) => setF({ ...f, blend: blend.map((x, k) => k === i ? { ...x, style_id: e.target.value } : x) })}>
            {styles.filter((s) => s.id !== initial?.id).map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}</select>
          <input type="range" min={0.1} max={1} step={0.1} value={b.weight} aria-label="Weight" onChange={(e) => setF({ ...f, blend: blend.map((x, k) => k === i ? { ...x, weight: +e.target.value } : x) })} />
          <button className="btn sm" onClick={() => setF({ ...f, blend: blend.filter((_, k) => k !== i) })}>Remove</button>
        </div>
      ))}
      {blend.length < 4 && <button className="btn sm" onClick={() => setF({ ...f, blend: [...blend, { style_id: styles[0].id, weight: 0.5 }] })}>Add a style to blend</button>}
      <h3>Prompt preview</h3>
      <div className="two-col"><Field label="Sample prompt"><input value={sample} onChange={(e) => setSample(e.target.value)} /></Field><div className="actions left"><button className="btn" disabled={!f.name} onClick={() => void preview()}>Preview</button></div></div>
      {out && <textarea readOnly rows={5} value={out} aria-label="Compiled prompt" />}
      <div className="modal-f"><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!f.name.trim()} onClick={() => void save()}>Save recipe</button></div>
    </Modal>
  );
}

export function Styles() {
  const { api, setError, goto, styleChoice, setStyleChoice, setNotice } = useApp();
  const [styles, setStyles] = useState<Style[]>([]);
  const [tab, setTab] = useState<Tab>("all");
  const [dim, setDim] = useState<(typeof DIMS)[number]>("all");
  const [q, setQ] = useState("");
  const [recent, setRecent] = useLocal<string[]>("cv.styles.recent", []);
  const [edit, setEdit] = useState<{ s: Style | null } | null>(null);
  const [confirmDel, setConfirmDel] = useState<string | null>(null);
  const dq = useDebounced(q.trim().toLowerCase(), 150);
  const load = () => api.styles().then(setStyles).catch((e) => setError(msg(e)));
  useEffect(() => { void load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const shown = useMemo(() => styles.filter((s) => {
    if (tab === "favorites" && !s.favorite) return false;
    if (tab === "recent" && !recent.includes(s.id)) return false;
    if (tab === "mine" && s.owner !== "user") return false;
    if (dim !== "all" && s.dimensionality !== dim) return false;
    return !dq || `${s.label} ${s.traits ?? ""} ${s.medium ?? ""}`.toLowerCase().includes(dq);
  }), [styles, tab, dim, dq, recent]);
  const use = (s: Style) => { setStyleChoice(s.id); setRecent([s.id, ...recent.filter((r) => r !== s.id)].slice(0, 12)); setNotice(`${s.label} selected. Use it in Create.`); goto("create"); };
  const fav = async (s: Style) => { try { await api.favoriteStyle(s.id, !s.favorite); await load(); } catch (e) { setError(msg(e)); } };
  const importFile = async (f: File) => { try { const doc = JSON.parse(await f.text()); const r = await api.importStyles(doc); await load(); setNotice(`${r.length} recipes imported.`); } catch (e) { setError(e instanceof SyntaxError ? "That file is not valid JSON." : msg(e)); } };

  return (
    <div className="page">
      <div className="work-head">
        <div><h1>Style Library</h1><p className="hint">Styles describe how an image looks. They work the same for dioramas and regular images, and across 2D, 3D and photo looks.</p></div>
        <div className="actions">
          <button className="btn primary" onClick={() => setEdit({ s: null })}>New recipe</button>
          <FileButton label="Import…" accept="application/json,.json" onFile={(f) => void importFile(f)} />
          <button className="btn" onClick={async () => { try { download("cozyverse-styles.json", await api.exportStyles()); } catch (e) { setError(msg(e)); } }}>Export mine</button>
        </div>
      </div>
      <div className="filters">
        <Seg label="Tab" value={tab} onChange={setTab} options={[{ value: "all", label: "All" }, { value: "favorites", label: "Favorites" }, { value: "recent", label: "Recent" }, { value: "mine", label: "My recipes" }]} />
        <Seg label="Dimension" value={dim} onChange={setDim} options={DIMS.map((d) => ({ value: d, label: d === "all" ? "Any look" : d }))} />
        <input className="search" type="search" placeholder="Search styles" aria-label="Search styles" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {shown.length === 0 ? <Notice>{tab === "mine" ? "You have no recipes yet. Create one, or import a file." : "No styles match."}</Notice> : (
        <ul className="style-grid">
          {shown.map((s) => (
            <li key={s.id} className={`style-card ${styleChoice === s.id ? "on" : ""}`}>
              <div className="style-h"><b>{s.label}</b>
                <button className="icon-btn" aria-label={s.favorite ? `Remove ${s.label} from favorites` : `Add ${s.label} to favorites`} aria-pressed={!!s.favorite} onClick={() => void fav(s)}>{s.favorite ? "★" : "☆"}</button></div>
              <div><Chip>{s.dimensionality}</Chip> {s.owner === "user" && <Chip tone="info">recipe v{s.version}</Chip>}</div>
              <p>{s.traits ?? [s.palette, s.lighting, s.texture].filter(Boolean).join("; ") ?? "Custom recipe"}</p>
              <div className="actions left">
                <button className="btn sm primary" onClick={() => use(s)}>Use</button>
                {s.owner === "user" && <button className="btn sm" onClick={() => setEdit({ s })}>Edit</button>}
                {s.owner === "user" && (confirmDel === s.id
                  ? <><button className="btn sm danger" onClick={async () => { try { await api.deleteStyle(s.id); setConfirmDel(null); await load(); } catch (e) { setError(msg(e)); } }}>Delete</button><button className="btn sm" onClick={() => setConfirmDel(null)}>Keep</button></>
                  : <button className="btn sm danger" onClick={() => setConfirmDel(s.id)}>Delete</button>)}
              </div>
            </li>
          ))}
        </ul>
      )}
      {edit && <Editor initial={edit.s} styles={styles} onClose={() => setEdit(null)} onSaved={() => void load()} />}
    </div>
  );
}
