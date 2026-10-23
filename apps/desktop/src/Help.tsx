import { useEffect, useRef, useState } from "react";
import { DOCS, QUICK_START } from "./helpContent";

export function Modal(props: { title: string; onClose: () => void; wide?: boolean; children: React.ReactNode }) {
  const ref = useRef<HTMLButtonElement>(null);
  const closeRef = useRef(props.onClose);
  closeRef.current = props.onClose;
  useEffect(() => {                       // run once: focus the dialog on open, never again while the user types
    ref.current?.focus();
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") closeRef.current(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) props.onClose(); }}>
      <div className={`modal ${props.wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={props.title}>
        <header className="modal-h">
          <h2>{props.title}</h2>
          <button ref={ref} className="icon-btn" aria-label={`Close ${props.title}`} onClick={props.onClose}>×</button>
        </header>
        <div className="modal-b">{props.children}</div>
      </div>
    </div>
  );
}

export function QuickStart(props: { onClose: () => void; onDocs: () => void }) {
  return (
    <Modal title="Quick start" onClose={props.onClose}>
      <ol className="steps">
        {QUICK_START.map((s, i) => (
          <li key={s.title}><span className="num">{i + 1}</span><div><b>{s.title}</b><p>{s.text}</p></div></li>
        ))}
      </ol>
      <p className="hint">Four orthographic camera renders plus one perspective render, all from the same 3D object.</p>
      <div className="modal-f">
        <button className="btn" onClick={props.onDocs}>Open documentation</button>
        <button className="btn primary" onClick={props.onClose}>Got it</button>
      </div>
    </Modal>
  );
}

export function Docs(props: { onClose: () => void; start?: string }) {
  const [id, setId] = useState(props.start ?? DOCS[0].id);
  const sec = DOCS.find((d) => d.id === id) ?? DOCS[0];
  return (
    <Modal title="Documentation" onClose={props.onClose} wide>
      <div className="docs">
        <nav aria-label="Documentation sections">
          {DOCS.map((d) => (
            <button key={d.id} className="nav-item" aria-current={d.id === id} onClick={() => setId(d.id)}>{d.title}</button>
          ))}
        </nav>
        <article>
          <h3>{sec.title}</h3>
          {sec.body.map((p, i) => <p key={i}>{p}</p>)}
        </article>
      </div>
    </Modal>
  );
}
