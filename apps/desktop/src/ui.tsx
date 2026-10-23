import { createContext, useContext, useEffect, useRef, useState } from "react";
import type { Api, Asset, BlenderInfo, Estimate, Job, Project } from "./api";
import { Modal } from "./Help";

export type Screen = "home" | "create" | "editor" | "decompose" | "studio" | "styles" | "models" | "exports" | "settings";

export interface Ctx {
  api: Api;
  blender: BlenderInfo | null;
  setBlender: (b: BlenderInfo) => void;
  project: Project | null;
  setProject: (p: Project | null) => void;
  assets: Asset[];
  refresh: () => Promise<void>;
  selectedId: string | null;
  setSelectedId: (id: string | null) => void;
  goto: (s: Screen) => void;
  screen: Screen;
  error: string | null;
  setError: (e: string | null) => void;
  notice: string | null;
  setNotice: (e: string | null) => void;
  /** Start a worker job and follow it in the drawer. */
  run: (label: string, start: () => Promise<{ job_id: string }>, onDone?: (job: Job) => void | Promise<void>) => Promise<void>;
  busy: boolean;
  job: Job | null;
  selectedSource: string | null;
  setSelectedSource: (id: string | null) => void;
  styleChoice: string | null;
  setStyleChoice: (id: string | null) => void;
}
export const AppCtx = createContext<Ctx | null>(null);
export const useApp = () => {
  const c = useContext(AppCtx);
  if (!c) throw new Error("AppCtx missing");
  return c;
};

export const msg = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Blob URL loaded with the session token (images cannot send headers), revoked on change. */
export function useBlob(loader: (() => Promise<string>) | null, deps: unknown[]): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let live = true; let made: string | null = null;
    if (!loader) { setUrl(null); return; }
    loader().then((u) => { made = u; if (live) setUrl(u); else URL.revokeObjectURL(u); }).catch(() => live && setUrl(null));
    return () => { live = false; if (made) URL.revokeObjectURL(made); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return url;
}

export function Chip(props: { tone?: "ok" | "bad" | "warn" | "info" | ""; children: React.ReactNode; title?: string }) {
  return <span className={`chip ${props.tone ?? ""}`} title={props.title}>{props.children}</span>;
}

export function Notice(props: { tone?: "warn" | "bad" | ""; children: React.ReactNode }) {
  return <div className={`notice ${props.tone ?? ""}`} role={props.tone === "bad" ? "alert" : undefined}>{props.children}</div>;
}

export function Field(props: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="field"><span>{props.label}</span>{props.children}{props.hint && <small className="hint">{props.hint}</small>}</label>
  );
}

export function Seg<T extends string>(props: { value: T; options: { value: T; label: string; title?: string }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="seg" role="group" aria-label={props.label}>
      {props.options.map((o) => (
        <button key={o.value} type="button" className={o.value === props.value ? "on" : ""} aria-pressed={o.value === props.value}
          title={o.title} onClick={() => props.onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  );
}

export function statusTone(s: string): "ok" | "bad" | "warn" | "info" | "" {
  if (["verified", "PASSED", "confirmed", "SUCCEEDED", "COMPLETE", "MESH_APPROVED", "PROXY_APPROVED"].includes(s)) return "ok";
  if (["disabled", "FAILED", "not_entitled", "deprecated"].includes(s)) return "bad";
  if (["unconfirmed", "NEEDS_REVIEW", "IMPORTED_PENDING_REVIEW", "experimental", "PARTIAL", "disputed", "unknown"].includes(s)) return "warn";
  if (["available", "DRAFT", "RUNNING"].includes(s)) return "info";
  return "";
}

export function money(v: number | null | undefined) {
  return v == null ? "unknown" : `$${v.toFixed(v < 1 ? 3 : 2)}`;
}

export function when(t: number | null | undefined) {
  if (!t) return "never";
  return new Date(t * 1000).toLocaleString();
}

/** Shows exactly what will be sent and what it costs. Paid runs need a ticked approval box. */
export function EstimateModal(props: {
  title: string; estimate: Estimate; confirmLabel: string; onCancel: () => void; onConfirm: (approved: boolean) => void; busy?: boolean;
}) {
  const e = props.estimate;
  const [ok, setOk] = useState(false);
  const need = e.requires_approval && !ok;
  const out = e.outbound;
  return (
    <Modal title={props.title} onClose={props.onCancel}>
      <div className="est">
        <div className="est-row"><span>Provider</span><b>{e.provider_label ?? e.provider}</b></div>
        {e.model && <div className="est-row"><span>Model</span><b>{typeof e.model === "string" ? e.model : e.model.label}
          {typeof e.model !== "string" && <> <Chip tone={statusTone(e.model.status)} title={e.model.status_detail}>{e.model.status}</Chip></>}</b></div>}
        <div className="est-row"><span>Estimated cost</span>
          <b>{e.cost.free ? "Free" : e.cost.usd_max == null ? "Unknown. The provider will bill your account." : money(e.cost.usd_max)}</b></div>
        <p className="hint">{e.cost.note}</p>
        <div className="est-row"><span>Leaves this computer</span><b>{out.leaves_device ? "Yes" : "No. Runs locally."}</b></div>
        {out.leaves_device && (
          <ul className="out">
            {typeof out.prompt_chars === "number" && out.prompt_chars > 0 && <li>Prompt text ({out.prompt_chars} characters)</li>}
            {out.references?.map((r: any) => <li key={r.source_id}>Reference image {r.source_id} · {(r.bytes / 1024).toFixed(0)} KB · re-encoded, metadata removed</li>)}
            {out.image && <li>Image {out.image.source_id ?? out.image.asset} · {(out.image.bytes / 1024).toFixed(0)} KB · re-encoded, metadata removed</li>}
            {out.mask_bytes > 0 && <li>Edit mask ({(out.mask_bytes / 1024).toFixed(0)} KB)</li>}
          </ul>
        )}
        {e.effective_prompt && (
          <label className="field"><span>Exact prompt that will be sent</span>
            <textarea readOnly rows={5} value={e.effective_prompt} /></label>
        )}
        {e.negative_prompt ? <p className="hint">Negative prompt: {e.negative_prompt}</p> : null}
        {e.warnings.map((w) => <Notice key={w} tone="warn">{w}</Notice>)}
        {e.requires_approval && (
          <label className="check"><input type="checkbox" checked={ok} onChange={(x) => setOk(x.target.checked)} />
            I approve sending this and any cost shown.</label>
        )}
      </div>
      <div className="modal-f">
        <button className="btn" onClick={props.onCancel}>Cancel</button>
        <button className="btn primary" disabled={need || props.busy} onClick={() => props.onConfirm(ok || !e.requires_approval)}>{props.confirmLabel}</button>
      </div>
    </Modal>
  );
}

export function useDebounced<T>(v: T, ms = 250): T {
  const [d, setD] = useState(v);
  useEffect(() => { const t = setTimeout(() => setD(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return d;
}

export function useLocal<T>(key: string, initial: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => {
    try { const s = localStorage.getItem(key); return s ? (JSON.parse(s) as T) : initial; } catch { return initial; }
  });
  const set = (n: T) => { setV(n); try { localStorage.setItem(key, JSON.stringify(n)); } catch { /* blocked */ } };
  return [v, set];
}

export function download(name: string, data: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function FileButton(props: { label: string; accept: string; onFile: (f: File) => void; disabled?: boolean; primary?: boolean; small?: boolean }) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <>
      <input ref={ref} type="file" accept={props.accept} hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) props.onFile(f); e.target.value = ""; }} />
      <button className={`btn ${props.primary ? "primary" : ""} ${props.small ? "sm" : ""}`} disabled={props.disabled} onClick={() => ref.current?.click()}>{props.label}</button>
    </>
  );
}
