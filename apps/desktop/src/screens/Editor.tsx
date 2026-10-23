import { useCallback, useEffect, useMemo, useState } from "react";
import type { Estimate, ProviderInfo, Source } from "../api";
import { ImageStage } from "../ImageStage";
import { Chip, EstimateModal, Field, Notice, Seg, msg, useApp, useBlob } from "../ui";
import { Thumb } from "./Create";

type Tool = "view" | "crop" | "text" | "flip" | "ai";

async function maskPng(box: number[] | null, w: number, h: number): Promise<string | undefined> {
  if (!box) return undefined;
  const k = Math.min(1, 512 / Math.max(w, h));
  const c = document.createElement("canvas");
  c.width = Math.max(32, Math.round(w * k)); c.height = Math.max(32, Math.round(h * k));
  const g = c.getContext("2d")!;
  g.fillStyle = "#000"; g.fillRect(0, 0, c.width, c.height);
  g.fillStyle = "#fff"; g.fillRect(box[0] * c.width, box[1] * c.height, box[2] * c.width, box[3] * c.height);
  return c.toDataURL("image/png").split(",")[1];
}

export function Editor() {
  const { api, project, setProject, goto, run, busy, setError, setNotice, selectedSource, setSelectedSource } = useApp();
  const [sources, setSources] = useState<Source[]>([]);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [tool, setTool] = useState<Tool>("view");
  const [compare, setCompare] = useState<string>("");
  const [split, setSplit] = useState(50);
  const [draft, setDraft] = useState<number[] | null>(null);
  const [pt, setPt] = useState<{ x: number; y: number } | null>(null);
  const [text, setText] = useState("OPEN");
  const [color, setColor] = useState("#ffffff");
  const [size, setSize] = useState(40);
  const [instr, setInstr] = useState("");
  const [modelId, setModelId] = useState("mock:scene-v1");
  const [estimate, setEstimate] = useState<{ est: Estimate; mask?: string } | null>(null);
  const [working, setWorking] = useState(false);

  const reload = useCallback(async () => { if (project) setSources(await api.sources(project.id)); }, [api, project?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { void reload().catch((e) => setError(msg(e))); }, [reload, setError]);
  useEffect(() => { api.providers().then(setProviders).catch(() => undefined); }, [api]);

  const cur = sources.find((s) => s.id === selectedSource) ?? sources[sources.length - 1] ?? null;
  const other = sources.find((s) => s.id === compare) ?? null;
  const url = useBlob(cur ? () => api.sourceUrl(cur.id) : null, [api, cur?.id]);
  const url2 = useBlob(other ? () => api.sourceUrl(other.id) : null, [api, other?.id]);
  const editModels = useMemo(() => providers.flatMap((p) => p.models.filter((m) => m.caps.edit && !["disabled", "deprecated", "not_entitled"].includes(m.status) && (!p.needs_key || p.configured)).map((m) => ({ p, m }))), [providers]);
  useEffect(() => { if (editModels.length && !editModels.some(({ m }) => m.id === modelId)) setModelId(editModels[0].m.id); }, [editModels, modelId]);

  if (!project) return <div className="page"><Notice>Open or create a project first.</Notice></div>;
  if (!cur) return <div className="page"><Notice>This project has no images yet. Generate one in Create, or import an image from Home.</Notice><button className="btn" onClick={() => goto("create")}>Go to Create</button></div>;

  const apply = async (op: Record<string, unknown>) => {
    setWorking(true); setError(null);
    try {
      const s = await api.editLocal(project.id, cur.id, op);
      await reload(); setSelectedSource(s.id); setTool("view"); setDraft(null); setPt(null);
      setNotice("Saved as a new version. The previous version is unchanged.");
    } catch (e) { setError(msg(e)); } finally { setWorking(false); }
  };
  const aiEstimate = async () => {
    setWorking(true); setError(null);
    try {
      const mask = await maskPng(draft, cur.width, cur.height);
      const m = editModels.find(({ m }) => m.id === modelId)?.m;
      const e = await api.estimateGenerate(project.id, { prompt: instr, model_id: modelId, mode: "edit", base_source_id: cur.id, n: 1,
        aspect_ratio: m?.caps.aspect_ratios.includes("16:9") ? "16:9" : "1:1", resolution: m?.caps.resolutions[0] ?? "1K",
        ...(mask && m?.caps.mask_edit ? { mask_png_b64: mask } : {}) });
      if (!e.requires_approval) await runEdit(e, true, mask && m?.caps.mask_edit ? mask : undefined); else setEstimate({ est: e, mask: mask && m?.caps.mask_edit ? mask : undefined });
    } catch (e) { setError(msg(e)); } finally { setWorking(false); }
  };
  const runEdit = async (e: Estimate, ok: boolean, mask?: string) => {
    setEstimate(null);
    await run("Editing image", () => api.generate(project.id, e.estimate_id, ok, mask), async (job) => {
      await reload();
      const id = job.result?.source_ids?.[0];
      if (job.state === "SUCCEEDED" && id) { setSelectedSource(id); setTool("view"); setNotice("Edit saved as a new version."); }
    });
  };
  const approve = async () => {
    try { await api.approveSource(project.id, cur.id); setProject(await api.project(project.id)); goto("decompose"); }
    catch (e) { setError(msg(e)); }
  };
  const m = cur.meta ?? {};
  const maskOk = editModels.find(({ m }) => m.id === modelId)?.m.caps.mask_edit;
  const boxes = tool === "crop" || (tool === "ai" && maskOk) ? [] : [];

  return (
    <div className="work two">
      <section className="work-main" aria-label="Editor canvas">
        <div className="work-head">
          <div><h1>Editor</h1><p className="hint">Every change is saved as a new version. Nothing is overwritten.</p></div>
          <Seg label="Tool" value={tool} onChange={(t) => { setTool(t); setDraft(null); setPt(null); }}
            options={[{ value: "view", label: "View" }, { value: "crop", label: "Crop" }, { value: "text", label: "Text" }, { value: "flip", label: "Flip" }, { value: "ai", label: "AI edit" }]} />
        </div>

        <div className="compare">
          <ImageStage src={url} width={cur.width} height={cur.height} alt="Selected version" boxes={boxes}
            draw={tool === "crop" || (tool === "ai" && !!maskOk)} draft={draft} onDraw={(b) => setDraft(b)}
            pick={tool === "text"} onPick={(x, y) => setPt({ x, y })} pickMark={pt}>
            {other && url2 && (
              <div className="swipe" style={{ clipPath: `inset(0 ${100 - split}% 0 0)` }}>
                <img src={url2} alt="Compared version" draggable={false} />
              </div>
            )}
          </ImageStage>
        </div>
        {other && (
          <div className="swipe-ctl">
            <span className="hint">A (compared) ◄</span>
            <input type="range" min={0} max={100} value={split} onChange={(e) => setSplit(+e.target.value)} aria-label="Compare split" />
            <span className="hint">► B (selected)</span>
          </div>
        )}
        <div className="filmstrip" role="list" aria-label="Versions">
          {sources.map((s) => <Thumb key={s.id} id={s.id} selected={s.id === cur.id} onClick={() => setSelectedSource(s.id)} badge={s.approved ? "✓" : undefined} />)}
        </div>
      </section>

      <aside className="panel side" aria-label="Editor tools">
        {tool === "view" && (
          <>
            <h2>Compare</h2>
            <Field label="Compare with another version">
              <select value={compare} onChange={(e) => setCompare(e.target.value)}>
                <option value="">None</option>
                {sources.filter((s) => s.id !== cur.id).map((s) => <option key={s.id} value={s.id}>{s.origin.toLowerCase()} · {new Date(s.created_at * 1000).toLocaleTimeString()}</option>)}
              </select>
            </Field>
          </>
        )}
        {tool === "crop" && (
          <>
            <h2>Crop</h2>
            <p className="hint">Drag a rectangle on the image, then apply.</p>
            <button className="btn primary" disabled={!draft || working} onClick={() => void apply({ op: "crop", box: draft })}>Apply crop</button>
          </>
        )}
        {tool === "text" && (
          <>
            <h2>Text overlay</h2>
            <p className="hint">Click on the image where the lettering should start. Use this for exact sign text instead of trusting generated letters.</p>
            <Field label="Text"><input value={text} maxLength={80} onChange={(e) => setText(e.target.value)} /></Field>
            <div className="two-col">
              <Field label="Size (px)"><input type="number" min={8} max={400} value={size} onChange={(e) => setSize(+e.target.value)} /></Field>
              <Field label="Colour"><input type="color" value={color} onChange={(e) => setColor(e.target.value)} /></Field>
            </div>
            <button className="btn primary" disabled={!pt || !text || working} onClick={() => pt && void apply({ op: "text_overlay", text, x: pt.x, y: pt.y, size, color })}>Apply text</button>
          </>
        )}
        {tool === "flip" && (
          <>
            <h2>Flip</h2>
            <p className="hint">Mirror the image left to right as a new version. Lettering will read backwards.</p>
            <button className="btn primary" disabled={working} onClick={() => void apply({ op: "flip" })}>Flip horizontally</button>
          </>
        )}
        {tool === "ai" && (
          <>
            <h2>AI edit</h2>
            {editModels.length === 0 ? <Notice>No editing model is available. Connect a provider key in Models and Costs.</Notice> : (
              <>
                <Field label="Model"><select value={modelId} onChange={(e) => setModelId(e.target.value)}>
                  {editModels.map(({ p, m }) => <option key={m.id} value={m.id}>{m.label} · {p.label}</option>)}</select></Field>
                <Field label="What should change?"><textarea rows={4} value={instr} onChange={(e) => setInstr(e.target.value)} placeholder="Make the sky a deep purple and add lanterns over the street" /></Field>
                {maskOk ? <p className="hint">{draft ? "Only the drawn region will be redrawn." : "Optional: drag a rectangle on the image to limit the edit to that region."} {draft && <button className="btn sm" onClick={() => setDraft(null)}>Clear region</button>}</p>
                  : <p className="hint">This model edits the whole image. It has no mask support.</p>}
                <button className="btn primary" disabled={!instr.trim() || working || busy} onClick={() => void aiEstimate()}>Review edit</button>
              </>
            )}
          </>
        )}

        <h2>Provenance</h2>
        <dl className="prov">
          <dt>Origin</dt><dd>{cur.origin.toLowerCase()}{cur.parent_id ? " (edit of another version)" : ""}</dd>
          <dt>Size</dt><dd>{cur.width} × {cur.height} · {cur.format} · {(cur.bytes / 1024).toFixed(0)} KB</dd>
          <dt>SHA-256</dt><dd><code>{cur.sha256.slice(0, 16)}…</code></dd>
          {m.provider && <><dt>Provider</dt><dd>{m.provider} · {m.model_slug}</dd></>}
          {m.style && <><dt>Style</dt><dd>{m.style.label} v{m.style.version}</dd></>}
          {m.seed != null && <><dt>Seed</dt><dd>{m.seed}</dd></>}
          {m.prompt && <><dt>Prompt</dt><dd>{m.prompt}</dd></>}
          {m.local_edit && <><dt>Edit</dt><dd>{m.local_edit}</dd></>}
        </dl>
        <div className="actions left">
          {cur.approved ? <Chip tone="ok">Approved for breakdown</Chip> : <button className="btn primary hero" onClick={() => void approve()}>Approve for Breakdown</button>}
          {cur.approved && <button className="btn primary" onClick={() => goto("decompose")}>Open Decompose</button>}
        </div>
      </aside>

      {estimate && <EstimateModal title="Review before editing" estimate={estimate.est} confirmLabel="Confirm and edit"
        onCancel={() => setEstimate(null)} onConfirm={(ok) => void runEdit(estimate.est, ok, estimate.mask)} />}
    </div>
  );
}
