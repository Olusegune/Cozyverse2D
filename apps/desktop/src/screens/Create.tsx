import { useCallback, useEffect, useMemo, useState } from "react";
import type { Caps, Estimate, ModelInfo, ProviderInfo, SceneMode, Source, Style } from "../api";
import { Modal } from "../Help";
import { Chip, EstimateModal, Field, FileButton, Notice, Seg, msg, useApp, useBlob } from "../ui";

const BLOCKED = ["disabled", "deprecated", "not_entitled"];
const GOALS = [["quick_draft", "Quick draft"], ["quality", "Quality first"], ["text", "Best text and signage"], ["character", "Character consistency"], ["budget", "Budget"]] as const;

export function Thumb({ id, selected, onClick, badge }: { id: string; selected: boolean; onClick: () => void; badge?: string }) {
  const { api } = useApp();
  const url = useBlob(() => api.sourceUrl(id, true), [api, id]);
  return (
    <button className={`thumb ${selected ? "on" : ""}`} onClick={onClick} aria-pressed={selected}>
      {url ? <img src={url} alt="" /> : <span />}
      {badge && <i>{badge}</i>}
    </button>
  );
}

export function SourceCanvas({ source, caption }: { source: Source | null; caption?: boolean }) {
  const { api } = useApp();
  const url = useBlob(source ? () => api.sourceUrl(source.id) : null, [api, source?.id]);
  if (!source) return <div className="canvas-empty"><b>Nothing generated yet</b><span>Write a prompt, choose a style and press Generate.</span></div>;
  const m = source.meta ?? {};
  return (
    <figure className="canvas">
      {url ? <img src={url} alt="Selected image" style={{ aspectRatio: `${source.width} / ${source.height}` }} /> : <div className="stage-empty">Loading…</div>}
      {caption !== false && (
        <figcaption>
          <Chip>{source.origin.toLowerCase()}</Chip>
          {m.provider && <Chip>{m.provider} · {m.model_slug ?? m.model_id}</Chip>}
          {m.seed != null && <Chip>seed {m.seed}</Chip>}
          <Chip>{source.width}×{source.height}</Chip>
          {source.approved && <Chip tone="ok">Approved for breakdown</Chip>}
        </figcaption>
      )}
    </figure>
  );
}

export function Create() {
  const { api, project, setProject, goto, run, busy, setError, setNotice, selectedSource, setSelectedSource, styleChoice, setStyleChoice } = useApp();
  const [sources, setSources] = useState<Source[]>([]);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [styles, setStyles] = useState<Style[]>([]);
  const [prompt, setPrompt] = useState("");
  const [negative, setNegative] = useState("");
  const [strength, setStrength] = useState(0.7);
  const [modelId, setModelId] = useState("mock:scene-v1");
  const [aspect, setAspect] = useState("16:9");
  const [res, setRes] = useState("1K");
  const [n, setN] = useState(2);
  const [seed, setSeed] = useState("");
  const [refs, setRefs] = useState<string[]>([]);
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [rec, setRec] = useState<string>("");
  const [working, setWorking] = useState(false);

  const reload = useCallback(async () => {
    if (!project) return;
    setSources(await api.sources(project.id));
  }, [api, project?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { void reload().catch((e) => setError(msg(e))); }, [reload, setError]);
  useEffect(() => { api.providers().then(setProviders).catch((e) => setError(msg(e))); api.styles().then(setStyles).catch(() => undefined); }, [api, setError]);

  const models = useMemo(() => providers.flatMap((p) => p.models.map((m) => ({ p, m }))).filter(({ m }) => m.caps.generate || BLOCKED.includes(m.status)), [providers]);
  const sel = models.find(({ m }) => m.id === modelId);
  const caps: Caps | null = sel?.m.caps ?? null;
  const unavailable = (p: ProviderInfo, m: ModelInfo) => BLOCKED.includes(m.status) ? m.status.replace("_", " ") : p.needs_key && !p.configured ? "connect key in Models and Costs" : "";

  useEffect(() => {          // keep options inside what the selected model supports
    if (!caps) return;
    if (!caps.aspect_ratios.includes(aspect)) setAspect(caps.aspect_ratios[0]);
    if (!caps.resolutions.includes(res)) setRes(caps.resolutions[0]);
    if (n > caps.max_variants) setN(caps.max_variants);
    if (refs.length > caps.max_refs) setRefs(refs.slice(0, caps.max_refs));
  }, [caps, aspect, res, n, refs]);

  if (!project) return <div className="page"><Notice>Open or create a project first.</Notice></div>;
  const chosen = sources.find((s) => s.id === selectedSource) ?? sources[sources.length - 1] ?? null;
  const style = styles.find((s) => s.id === styleChoice);
  const body = () => ({
    prompt, negative_prompt: negative, style_id: styleChoice, style_strength: styleChoice ? strength : undefined, model_id: modelId,
    aspect_ratio: aspect, resolution: res, n, seed: caps?.seed && seed !== "" ? Number(seed) : undefined, reference_ids: refs,
  });
  const ready = prompt.trim().length > 0 && !!sel && !unavailable(sel.p, sel.m);

  const startGenerate = async (est: Estimate, approved: boolean) => {
    setEstimate(null);
    await run("Generating images", () => api.generate(project.id, est.estimate_id, approved), async (job) => {
      await reload();
      const ids: string[] = job.result?.source_ids ?? [];
      if (job.state === "SUCCEEDED" && ids[0]) { setSelectedSource(ids[0]); setNotice(`${ids.length} image(s) added. Compare them, then approve one for breakdown.`); }
    });
  };
  const review = async () => {
    setWorking(true); setError(null);
    try {
      const e = await api.estimateGenerate(project.id, body());
      if (!e.requires_approval) await startGenerate(e, true); else setEstimate(e);
    } catch (x) { setError(msg(x)); } finally { setWorking(false); }
  };
  const showPrompt = async () => {
    try {
      const r = await api.compileStyle({ style_id: styleChoice, prompt, negative_prompt: negative, scene_mode: project.scene_mode,
        model_id: modelId, strength: styleChoice ? strength : undefined });
      setPreview(r.prompt + (r.negative_prompt ? `\n\nNegative: ${r.negative_prompt}` : "") + (r.warnings.length ? `\n\nNotes: ${r.warnings.join(" ")}` : ""));
    } catch (x) { setError(msg(x)); }
  };
  const suggest = async (goal: string) => {
    setRec(goal);
    try {
      const r = await api.recommend(goal);
      if (r[0]) { setModelId(r[0].model_id); setNotice(`Suggested ${r[0].label}: ${r[0].reason}. Nothing is sent until you confirm.`); }
      else setNotice("No connected model is available for that goal. Connect a key in Models and Costs, or use the built-in generator.");
    } catch (x) { setError(msg(x)); }
  };
  const switchMode = async (mode: SceneMode) => {
    try {
      setProject(await api.patchProject(project.id, { scene_mode: mode, ...(mode === "REGULAR_IMAGE" ? { base_mode: "NONE" } : {}) }));
      if (sources.length > 0) setNotice(`Switched to ${mode === "DIORAMA" ? "Diorama" : "Regular image"}. All your images, edits, items and styles are kept. Existing renders that used a base may need another look.`);
    }
    catch (x) { setError(msg(x)); }
  };
  const addRef = async (f: File) => {
    try { const s = await api.uploadSource(project.id, f); await reload(); setRefs([...refs, s.id].slice(0, caps?.max_refs ?? 0)); }
    catch (x) { setError(msg(x)); }
  };
  const approve = async () => {
    if (!chosen) return;
    try { await api.approveSource(project.id, chosen.id); setProject(await api.project(project.id)); await reload(); goto("decompose"); }
    catch (x) { setError(msg(x)); }
  };
  const grouped = ["2D", "2.5D", "3D", "photo"].map((d) => [d, styles.filter((s) => s.dimensionality === d)] as const).filter(([, l]) => l.length);

  return (
    <div className="work two">
      <section className="work-main" aria-label="Canvas">
        <div className="work-head">
          <div><h1>Create</h1><p className="hint">{project.name}</p></div>
          <Seg label="Scene mode" value={project.scene_mode} onChange={(m) => void switchMode(m)}
            options={[{ value: "DIORAMA", label: "Diorama", title: "Miniature on a display base" }, { value: "REGULAR_IMAGE", label: "Regular image", title: "No base is added" }]} />
        </div>
        <SourceCanvas source={chosen} />
        <div className="filmstrip" role="list" aria-label="Variations">
          {sources.map((s) => (
            <Thumb key={s.id} id={s.id} selected={s.id === chosen?.id} onClick={() => setSelectedSource(s.id)}
              badge={s.approved ? "✓" : s.origin === "GENERATED" ? "AI" : s.origin === "EDITED" ? "ED" : "IN"} />
          ))}
        </div>
        <div className="actions left">
          <button className="btn" disabled={!chosen} onClick={() => goto("editor")}>Edit</button>
          <button className="btn primary hero" disabled={!chosen} onClick={() => void approve()}>Approve for Breakdown</button>
        </div>
        <p className="hint">Generating never starts a breakdown or any 3D work. You approve one image first.</p>
      </section>

      <aside className="panel side" aria-label="Generation settings">
        <h2>Prompt</h2>
        <Field label="What should it look like?"><textarea rows={6} value={prompt} onChange={(e) => setPrompt(e.target.value)}
          placeholder="A rainy night street market with a neon BAR sign, warm shop windows and a yellow taxi" /></Field>
        <details><summary>Negative prompt</summary>
          <Field label="Things to avoid" hint={caps && !caps.negative_prompt ? "This model has no negative field. It is added to the prompt as avoid-text." : undefined}>
            <textarea rows={2} value={negative} onChange={(e) => setNegative(e.target.value)} placeholder="blurry, extra fingers, text artifacts" /></Field>
        </details>

        <h2>Style</h2>
        <Field label="Art style">
          <select value={styleChoice ?? ""} onChange={(e) => setStyleChoice(e.target.value || null)}>
            <option value="">No style (use my words only)</option>
            {styles.filter((s) => s.owner === "user").length > 0 && <optgroup label="My recipes">{styles.filter((s) => s.owner === "user").map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}</optgroup>}
            {grouped.map(([d, l]) => <optgroup key={d} label={d === "photo" ? "Photo and cinematic" : `${d} styles`}>{l.filter((s) => s.owner === "app").map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}</optgroup>)}
          </select>
        </Field>
        {style && <p className="hint">{style.traits ?? "Custom recipe"}</p>}
        {styleChoice && <Field label={`Style strength: ${strength.toFixed(2)}`}><input type="range" min={0} max={1} step={0.05} value={strength} onChange={(e) => setStrength(+e.target.value)} /></Field>}
        <button className="btn sm" onClick={() => goto("styles")}>Open style library</button>

        <h2>Model</h2>
        <Field label="Provider and model">
          <select value={modelId} onChange={(e) => setModelId(e.target.value)}>
            {providers.map((p) => (
              <optgroup key={p.id} label={p.label + (p.needs_key && !p.configured ? " (no key)" : "")}>
                {p.models.filter((m) => m.caps.generate || BLOCKED.includes(m.status)).map((m) => {
                  const why = unavailable(p, m);
                  return <option key={m.id} value={m.id} disabled={!!why}>{m.label} · {m.status}{why ? ` · ${why}` : ""}</option>;
                })}
              </optgroup>
            ))}
          </select>
        </Field>
        {sel && sel.m.status_detail && <p className="hint">{sel.m.status_detail}</p>}
        <div className="suggest"><span className="hint">Suggest a model for:</span>
          {GOALS.map(([g, l]) => <button key={g} className={`btn sm ${rec === g ? "on" : ""}`} onClick={() => void suggest(g)}>{l}</button>)}
        </div>
        {caps && (
          <>
            <div className="two-col">
              <Field label="Aspect ratio"><select value={aspect} onChange={(e) => setAspect(e.target.value)}>{caps.aspect_ratios.map((a) => <option key={a}>{a}</option>)}</select></Field>
              <Field label="Resolution"><select value={res} onChange={(e) => setRes(e.target.value)}>{caps.resolutions.map((a) => <option key={a}>{a}</option>)}</select></Field>
            </div>
            <div className="two-col">
              <Field label="Variations"><select value={n} onChange={(e) => setN(+e.target.value)}>{Array.from({ length: caps.max_variants }, (_, i) => i + 1).map((v) => <option key={v}>{v}</option>)}</select></Field>
              {caps.seed ? <Field label="Seed (optional)"><input inputMode="numeric" value={seed} onChange={(e) => setSeed(e.target.value.replace(/\D/g, ""))} /></Field>
                : <Field label="Seed" hint="Not supported by this model."><input disabled placeholder="n/a" /></Field>}
            </div>
            {caps.max_refs > 0 ? (
              <Field label={`Reference images (${refs.length}/${caps.max_refs})`}>
                <div className="refs">
                  {sources.map((s) => <Thumb key={s.id} id={s.id} selected={refs.includes(s.id)} onClick={() => setRefs(refs.includes(s.id) ? refs.filter((r) => r !== s.id) : refs.length < caps.max_refs ? [...refs, s.id] : refs)} />)}
                </div>
                <FileButton label="Add reference image…" accept="image/png,image/jpeg,image/webp" small onFile={(f) => void addRef(f)} />
              </Field>
            ) : <p className="hint">This model does not take reference images.</p>}
          </>
        )}
        <div className="actions left">
          <button className="btn" disabled={!prompt.trim()} onClick={() => void showPrompt()}>Preview prompt</button>
          <button className="btn primary hero" disabled={!ready || working || busy} onClick={() => void review()}>
            {sel?.p.needs_key ? "Review and generate" : "Generate"}
          </button>
        </div>
        {sel && !sel.p.needs_key && <p className="hint">Built-in test generator: free, offline, deterministic. Not an AI model.</p>}
      </aside>

      {estimate && <EstimateModal title="Review before generating" estimate={estimate} confirmLabel="Confirm and generate"
        onCancel={() => setEstimate(null)} onConfirm={(ok) => void startGenerate(estimate, ok)} />}
      {preview && <Modal title="Exact prompt" onClose={() => setPreview(null)}><textarea readOnly rows={10} value={preview} /><div className="modal-f"><button className="btn" onClick={() => setPreview(null)}>Close</button></div></Modal>}
    </div>
  );
}
