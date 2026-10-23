import { useCallback, useEffect, useState } from "react";
import type { ExportRun } from "../api";
import { canOpenFolder, openFolder } from "../api";
import { Chip, Field, Notice, download, msg, statusTone, useApp, when } from "../ui";

export function Exports() {
  const { api, project, assets, run, busy, setError } = useApp();
  const [runs, setRuns] = useState<ExportRun[]>([]);
  const [sheet, setSheet] = useState(false);
  const [override, setOverride] = useState(false);
  const [reason, setReason] = useState("");
  const load = useCallback(async () => { if (project) setRuns(await api.exports(project.id)); }, [api, project?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { void load().catch((e) => setError(msg(e))); }, [load, setError]);
  if (!project) return <div className="page"><Notice>Open or create a project first.</Notice></div>;

  const threeD = assets.filter((a) => a.fixture !== "custom" || a.goal === "3D_PROXY" || a.goal === "3D_RECONSTRUCTION");
  const twoD = assets.filter((a) => a.fixture === "custom" && (a.goal === "2D_LAYER_EXPORT" || a.goal === "REFERENCE_ONLY"));
  const approved = (a: (typeof assets)[number]) => (["front", "back", "left", "right", "perspective"] as const).every((v) => a.views[v].status === "PASSED" && !a.views[v].stale);
  const go = () => void run("Exporting ZIP", () => api.exportProject(project.id, { include_contact_sheet: sheet, override_critical: override, override_reason: override ? reason : "" }), () => load());

  return (
    <div className="page">
      <div className="work-head"><div><h1>Exports</h1><p className="hint">{project.name}</p></div>
        <div className="actions">
          <button className="btn" title="A small file that opens this project on this computer. To share work, use Export ZIP." onClick={async () => { try { download(`${project.slug}.cvbd`, await api.projectFile(project.id)); } catch (e) { setError(msg(e)); } }}>Save project file…</button>
          <button className="btn primary hero" disabled={busy || (override && reason.trim().length < 10)} onClick={go}>Export ZIP</button></div></div>

      <div className="grid2">
        <section className="panel" aria-label="What will be included">
          <h2>What is included</h2>
          <ul className="plain">
            <li><b>{threeD.length}</b> 3D assets: {threeD.filter(approved).length} fully approved, {threeD.filter((a) => !approved(a)).length} not yet.</li>
            <li><b>{twoD.length}</b> 2D items (layers and unverified references).</li>
            <li>Approved source image, its lineage and the inventory with history.</li>
          </ul>
          <p className="hint">The export says PARTIAL, and lists exactly why, until every asset is complete. It never claims more than exists.</p>
          <label className="check"><input type="checkbox" checked={sheet} onChange={(e) => setSheet(e.target.checked)} />Also add a contact sheet (supplementary only; the five files stay separate)</label>
        </section>
        <section className="panel" aria-label="Override">
          <h2>Critical findings</h2>
          <p className="hint">Unresolved critical image findings block a COMPLETE export. You can override that with a written reason, which is saved in the report.</p>
          <label className="check"><input type="checkbox" checked={override} onChange={(e) => setOverride(e.target.checked)} />Override critical findings</label>
          {override && <Field label="Reason (at least 10 characters)"><input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} /></Field>}
        </section>
      </div>

      <section className="panel" aria-label="Past exports">
        <h2>Past exports</h2>
        {runs.length === 0 ? <p className="hint">No exports yet.</p> : (
          <ul className="plist">
            {runs.map((r) => (
              <li key={r.id} className="exp">
                <div><b>{when(r.created_at)}</b><small>{r.path}</small></div>
                <Chip tone={statusTone(r.status)}>{r.status}</Chip>
                {r.override && <Chip tone="warn" title={r.override.reason}>override</Chip>}
                <button className="btn sm" disabled={!canOpenFolder() || !r.folder} title={canOpenFolder() ? "Open the folder" : "Available in the desktop app"} onClick={() => r.folder && void openFolder(r.folder)}>Open Folder</button>
                {Object.keys(r.incomplete).length > 0 && (
                  <details className="why"><summary>Why partial?</summary>
                    {Object.entries(r.incomplete).map(([code, rs]) => <p key={code}><b>{code}</b>: {rs.join("; ")}</p>)}</details>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
