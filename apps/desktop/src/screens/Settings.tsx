import { useState } from "react";
import { Chip, Field, useApp, useLocal } from "../ui";

export function applyMotion(off: boolean) { document.documentElement.classList.toggle("reduce-motion", off); }

export function Settings() {
  const { api, blender, setBlender, setError, setNotice } = useApp();
  const [path, setPath] = useState("");
  const [splashOff, setSplashOff] = useLocal<boolean>("cv.splash.off.ui", false);
  const [motionOff, setMotionOff] = useLocal<boolean>("cv.motion.off", false);
  const save = async () => { try { setBlender(await api.setBlender(path)); setNotice("Blender path saved."); } catch { setError("That path is not a working Blender executable."); } };
  return (
    <div className="page narrow">
      <div className="work-head"><div><h1>Settings</h1></div></div>
      <section className="panel" aria-label="Blender">
        <h2>Blender</h2>
        <p>{blender?.found ? <Chip tone="ok">Blender {blender.version} · {blender.device}</Chip> : <Chip tone="bad">Not found</Chip>} <small className="hint">{blender?.path}</small></p>
        <Field label="Executable path" hint="Used for every render. Blender 4.x and 5.x are supported.">
          <input value={path} placeholder={blender?.path ?? "C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe"} onChange={(e) => setPath(e.target.value)} /></Field>
        <button className="btn" disabled={!path} onClick={() => void save()}>Test and save</button>
      </section>
      <section className="panel" aria-label="Appearance">
        <h2>Appearance</h2>
        <label className="check"><input type="checkbox" checked={splashOff} onChange={(e) => { setSplashOff(e.target.checked); try { e.target.checked ? localStorage.setItem("cv.splash.off", "1") : localStorage.removeItem("cv.splash.off"); } catch { /* blocked */ } }} />Do not show the splash screen on startup</label>
        <label className="check"><input type="checkbox" checked={motionOff} onChange={(e) => { setMotionOff(e.target.checked); applyMotion(e.target.checked); }} />Reduce motion</label>
      </section>
      <section className="panel" aria-label="Privacy">
        <h2>Privacy</h2>
        <ul className="plain">
          <li>No telemetry. Nothing is sent anywhere unless you approve a provider request, and each one shows exactly what leaves this computer.</li>
          <li>Your source images stay on this computer. Originals are never modified.</li>
          <li>Provider keys are stored in the Windows credential store.</li>
          <li>Local Blender renders are free and unlimited.</li>
        </ul>
      </section>
    </div>
  );
}
