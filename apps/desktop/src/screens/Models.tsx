import { useCallback, useEffect, useState } from "react";
import type { Budget, ModelInfo, ProviderInfo } from "../api";
import { Chip, Field, Notice, money, msg, statusTone, useApp, when } from "../ui";

type PM = ModelInfo & { price_usd?: number | null };

function caps(m: PM) {
  const c = m.caps;
  return [c.generate && "generate", c.edit && "edit", c.mask_edit && "mask", c.max_refs > 0 && `${c.max_refs} refs`, c.seed && "seed", c.transparency && "transparent", c.max_variants > 1 && `up to ${c.max_variants}`]
    .filter(Boolean).join(" · ") || "none";
}

function Price({ m, unit, onSaved }: { m: PM; unit: string; onSaved: () => void }) {
  const { api, setError } = useApp();
  const [v, setV] = useState(m.price_usd != null ? String(m.price_usd) : "");
  useEffect(() => setV(m.price_usd != null ? String(m.price_usd) : ""), [m.price_usd]);
  return (
    <div className="price">
      <input inputMode="decimal" aria-label={`Price per ${unit} for ${m.label}`} placeholder="unknown" value={v} onChange={(e) => setV(e.target.value.replace(/[^\d.]/g, ""))} />
      <button className="btn sm" onClick={async () => { try { await api.setPrice(m.id, v === "" ? null : Number(v)); onSaved(); } catch (e) { setError(msg(e)); } }}>Save</button>
    </div>
  );
}

function ProviderCard({ p, unit, reload }: { p: ProviderInfo; unit: string; reload: () => void }) {
  const { api, setError, setNotice } = useApp();
  const [key, setKey] = useState("");
  const [result, setResult] = useState("");
  return (
    <section className="panel pcard" aria-label={p.label}>
      <div className="panel-h">
        <h2>{p.label}</h2>
        {p.needs_key ? <Chip tone={p.configured ? "ok" : ""}>{p.configured ? "Key saved" : "No key"}</Chip> : <Chip tone="ok">Built in · free</Chip>}
      </div>
      {p.needs_key && (
        <div className="keyrow">
          <input type="password" autoComplete="off" spellCheck={false} placeholder={p.configured ? "A key is saved. Paste a new one to replace it." : "Paste your API key"} aria-label={`${p.label} API key`}
            value={key} onChange={(e) => setKey(e.target.value)} />
          <button className="btn sm primary" disabled={key.trim().length < 8} onClick={async () => { try { await api.setKey(p.id, key.trim()); setKey(""); setNotice("Key saved in the Windows credential store."); reload(); } catch (e) { setError(msg(e)); } }}>Save key</button>
          <button className="btn sm" disabled={!p.configured} onClick={async () => { try { setResult((await api.refreshProvider(p.id)).detail); reload(); } catch (e) { setResult(""); setError(msg(e)); } }}>Check connection</button>
          <button className="btn sm danger" disabled={!p.configured} onClick={async () => { try { await api.deleteKey(p.id); reload(); } catch (e) { setError(msg(e)); } }}>Remove key</button>
        </div>
      )}
      {result && <p className="hint">{result}</p>}
      <table className="tbl">
        <thead><tr><th>Model</th><th>Status</th><th>Supports</th><th>Last checked</th>{p.needs_key && <th>Price per {unit}</th>}</tr></thead>
        <tbody>
          {(p.models as PM[]).map((m) => (
            <tr key={m.id}>
              <td><b>{m.label}</b><br /><code>{m.slug}</code></td>
              <td><Chip tone={statusTone(m.status)} title={m.status_detail}>{m.status.replace("_", " ")}</Chip>{m.status_detail && <small className="hint block">{m.status_detail}</small>}</td>
              <td>{caps(m)}</td>
              <td>{m.last_checked ? when(m.last_checked) : "never"}</td>
              {p.needs_key && <td><Price m={m} unit={unit} onSaved={reload} /></td>}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export function Models() {
  const { api, setError } = useApp();
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [mesh, setMesh] = useState<ProviderInfo[]>([]);
  const [budget, setBudget] = useState<Budget | null>(null);
  const [usage, setUsage] = useState<any[]>([]);
  const [caps_, setCaps] = useState({ daily: "", monthly: "" });
  const load = useCallback(async () => {
    try {
      const [p, a, b, u] = await Promise.all([api.providers(), api.aiProviders(), api.budget(), api.usage()]);
      setProviders(p); setMesh(a.mesh); setBudget(b); setUsage(u);
      setCaps({ daily: b.daily_cap_usd != null ? String(b.daily_cap_usd) : "", monthly: b.monthly_cap_usd != null ? String(b.monthly_cap_usd) : "" });
    } catch (e) { setError(msg(e)); }
  }, [api, setError]);
  useEffect(() => { void load(); }, [load]);
  const saveBudget = async (patch: Parameters<typeof api.setBudget>[0]) => { try { setBudget(await api.setBudget(patch)); } catch (e) { setError(msg(e)); } };
  const num = (s: string) => (s === "" ? null : Number(s));

  return (
    <div className="page">
      <div className="work-head"><div><h1>Models and Costs</h1><p className="hint">Connect your own provider accounts. Keys live in the Windows credential store, never in project files or logs.</p></div></div>
      <Notice>A model is only marked verified after a generation really succeeds with it. Prices are not guessed: enter your own so estimates and caps can use them.</Notice>

      {budget && (
        <section className="panel" aria-label="Spending controls">
          <div className="panel-h"><h2>Spending controls</h2><Chip>Today {money(budget.spent_day_usd)} · 30 days {money(budget.spent_month_usd)}</Chip></div>
          <div className="grid3">
            <Field label="Daily cap (USD)" hint="Blank means no cap."><input inputMode="decimal" value={caps_.daily} onChange={(e) => setCaps({ ...caps_, daily: e.target.value.replace(/[^\d.]/g, "") })} /></Field>
            <Field label="Monthly cap (USD)"><input inputMode="decimal" value={caps_.monthly} onChange={(e) => setCaps({ ...caps_, monthly: e.target.value.replace(/[^\d.]/g, "") })} /></Field>
            <div className="actions left"><button className="btn" onClick={() => void saveBudget({ daily_cap_usd: num(caps_.daily), monthly_cap_usd: num(caps_.monthly) })}>Save caps</button></div>
          </div>
          <label className="check"><input type="checkbox" checked={budget.local_only} onChange={(e) => void saveBudget({ local_only: e.target.checked })} />
            Local-only mode: block every online provider. Local Blender renders and the built-in generator keep working.</label>
          <p className="hint">Caps only apply to estimates that have a known price. Unknown prices always need your explicit approval.</p>
        </section>
      )}

      <h2 className="sec">Image models</h2>
      {providers.map((p) => <ProviderCard key={p.id} p={p} unit="image" reload={() => void load()} />)}
      <h2 className="sec">3D model providers</h2>
      {mesh.map((p) => <ProviderCard key={p.id} p={p} unit="task" reload={() => void load()} />)}
      <p className="hint">Object detection with Google uses your Google key above. Detection never runs without your approval.</p>

      <section className="panel" aria-label="Usage">
        <h2>Recent provider activity</h2>
        {usage.length === 0 ? <p className="hint">Nothing yet. Local work never appears here.</p> : (
          <table className="tbl"><thead><tr><th>When</th><th>Provider</th><th>Model</th><th>Kind</th><th>Estimate</th><th>State</th></tr></thead>
            <tbody>{usage.slice(0, 30).map((u) => <tr key={u.id}><td>{when(u.at)}</td><td>{u.provider}</td><td><code>{u.model_id}</code></td><td>{u.kind}</td><td>{money(u.est_usd)}</td>
              <td><Chip tone={u.state === "completed" ? "ok" : u.state === "failed" ? "bad" : "warn"} title={u.state === "orphaned_possible" ? "Stopped locally. The provider may still finish and bill." : undefined}>{u.state.replace("_", " ")}</Chip></td></tr>)}</tbody></table>
        )}
      </section>
    </div>
  );
}
