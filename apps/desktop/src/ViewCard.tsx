import { useEffect, useState } from "react";
import { Api, Asset, ViewName, ViewStatus } from "./api";

const LABEL: Record<ViewName, string> = { front: "FRONT", back: "BACK", left: "LEFT", right: "RIGHT", perspective: "PERSPECTIVE" };
const STATUS: Record<ViewStatus, { text: string; tone: string }> = {
  BLOCKED: { text: "Blocked", tone: "" }, DRAFT: { text: "Draft", tone: "info" },
  NEEDS_REVIEW: { text: "Needs review", tone: "warn" }, PASSED: { text: "Approved", tone: "ok" },
  FAILED: { text: "Failed", tone: "bad" },
};

export function ViewCard(props: {
  asset: Asset; view: ViewName; api: Api; disabled: boolean;
  onReview: (s: "PASSED" | "FAILED") => void;
}) {
  const { asset, view, api } = props;
  const info = asset.views[view];
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let live = true; let made: string | null = null;
    if (!info.path) { setUrl(null); return; }
    api.imageUrl(asset.id, view).then((u) => { made = u; if (live) setUrl(u); else URL.revokeObjectURL(u); }).catch(() => setUrl(null));
    return () => { live = false; if (made) URL.revokeObjectURL(made); };
  }, [api, asset.id, view, info.sha256, info.path]);

  const s = STATUS[info.status];
  const m = info.meta;
  const rendering = (info.qa?.findings ?? []).some((f) => f.code === "RENDER_PENDING");
  const crit = (info.qa?.findings ?? []).filter((f) => f.severity === "critical" && f.code !== "RENDER_PENDING");
  const tip = m ? [
    `${info.camera_type === "ORTHO" ? "Orthographic" : "Perspective"} camera`,
    m.forward ? `looks along (${m.forward.map((n) => n.toFixed(2)).join(", ")})` : "",
    info.camera_type === "ORTHO" && m.ortho_scale ? `ortho scale ${m.ortho_scale.toFixed(3)} m` : "",
    info.camera_type === "PERSP" && m.lens_mm ? `${m.lens_mm} mm · ${m.quadrant} · ${m.elevation_deg}° elevation` : "",
    `base: ${m.base_policy}`, `${m.engine} · Blender ${m.blender_version}`, `revision ${info.model_revision?.slice(0, 10)}`,
  ].filter(Boolean).join("\n") : "Not rendered yet";
  const canReview = !!info.path && !info.stale && info.status !== "BLOCKED" && info.status !== "FAILED" && !props.disabled;

  return (
    <article className={`card ${view === "perspective" ? "persp" : ""}`} title={tip} aria-label={`${LABEL[view]} view`}>
      <div className="card-h">
        <b>{LABEL[view]}</b>
        <span className={`badge ${info.camera_type === "PERSP" ? "persp" : ""}`}>{info.camera_type === "ORTHO" ? "ORTHO" : "PERSP"}</span>
        <span style={{ flex: 1 }} />
        <span className={`chip ${s.tone}`}>{s.text}</span>
      </div>
      <div className="frame">
        {url ? <img src={url} alt={`${LABEL[view]} render of ${asset.name}`} /> : (
          <div className="empty">{rendering ? "Rendering…" : asset.mesh_status === "PROXY_APPROVED" ? "Not rendered yet" : "Blocked: no 3D model yet"}</div>
        )}
        {url && info.stale && !rendering && <div className="stale"><div><b>Stale</b><br />The model changed. Re-render this view.</div></div>}
      </div>
      <div className="card-f">
        <div className="row">
          <code>views/{view}.png</code>
          {m?.resolution && <span>{m.resolution[0]}×{m.resolution[1]}</span>}
          {m?.base_policy && <span>base: {m.base_policy.replace("_", " ")}</span>}
        </div>
        {rendering && <div className="row">Rendering…</div>}
        {crit.length > 0 && <div className="row" style={{ color: "var(--danger)" }}>{crit.map((f) => f.code).join(", ")}</div>}
        {view !== "front" && view !== "perspective" && info.path && <div className="row">Inferred: unseen wall</div>}
        <div className="row">
          <button className="btn sm" disabled={!canReview || info.status === "PASSED"} onClick={() => props.onReview("PASSED")}>Approve</button>
          <button className="btn sm danger" disabled={!canReview || info.status === "FAILED"} onClick={() => props.onReview("FAILED")}>Reject</button>
        </div>
      </div>
    </article>
  );
}
