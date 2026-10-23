import { useEffect, useRef, useState } from "react";
import splash from "./assets/splash.webp";

const KEY = "cv.splash.off";
const read = () => { try { return localStorage.getItem(KEY) === "1"; } catch { return false; } };
const write = (v: boolean) => { try { v ? localStorage.setItem(KEY, "1") : localStorage.removeItem(KEY); } catch { /* storage blocked */ } };

/** Centered card (never full-window). Closes via X, Esc, a click on the backdrop, or automatically once the app is ready. */
export function Splash(props: { ready: boolean; error: string | null; onRetry: () => void; onClose: () => void }) {
  const [skipFuture, setSkipFuture] = useState(read());
  const [minElapsed, setMinElapsed] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);
  const { ready, error, onClose } = props;

  useEffect(() => { const t = setTimeout(() => setMinElapsed(true), 1800); return () => clearTimeout(t); }, []);
  useEffect(() => { if (ready && minElapsed && !error) onClose(); }, [ready, minElapsed, error, onClose]);
  useEffect(() => {
    closeRef.current?.focus();
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  return (
    <div className="splash-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="splash-card" role="dialog" aria-modal="true" aria-label="CozyVerse Breakdown">
        <button ref={closeRef} className="splash-x" aria-label="Skip splash screen" title="Skip (Esc)" onClick={onClose}>×</button>
        <img src={splash} alt="CozyVerse Breakdown. Create. Decompose. Rebuild." draggable={false} />
        <div className="splash-foot">
          {error ? (
            <div className="notice bad" role="alert" style={{ flex: 1 }}>
              Could not reach the local worker: {error}{" "}
              <button className="btn sm" onClick={props.onRetry}>Retry</button>
            </div>
          ) : (
            <div className="load" aria-hidden><i /></div>
          )}
          <label className="splash-skip">
            <input type="checkbox" checked={skipFuture} onChange={(e) => { setSkipFuture(e.target.checked); write(e.target.checked); }} />
            Don't show on startup
          </label>
        </div>
      </div>
    </div>
  );
}

export const splashDisabled = read;
