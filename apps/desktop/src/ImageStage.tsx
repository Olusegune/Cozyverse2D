import { useRef, useState } from "react";

export interface StageBox { id: string; bbox: number[]; label?: string; tone?: "ok" | "warn" | "info" | "bad" | "dim"; dashed?: boolean }

/** An image with an overlay in normalized (0..1) coordinates. Supports drawing a box and picking a point. */
export function ImageStage(props: {
  src: string | null; width: number; height: number; alt: string;
  boxes?: StageBox[]; selectedId?: string | null; onSelect?: (id: string | null) => void;
  draw?: boolean; onDraw?: (bbox: number[]) => void; draft?: number[] | null;
  pick?: boolean; onPick?: (x: number, y: number) => void; pickMark?: { x: number; y: number } | null;
  fit?: "contain" | "cover"; className?: string; children?: React.ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);

  const norm = (e: React.PointerEvent) => {
    const r = box.current!.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) };
  };
  const down = (e: React.PointerEvent) => {
    if (!props.draw && !props.pick) return;
    (e.target as Element).setPointerCapture?.(e.pointerId);
    const p = norm(e);
    if (props.pick) { props.onPick?.(p.x, p.y); return; }
    setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y });
  };
  const move = (e: React.PointerEvent) => { if (drag) { const p = norm(e); setDrag({ ...drag, x1: p.x, y1: p.y }); } };
  const up = () => {
    if (!drag) return;
    const x = Math.min(drag.x0, drag.x1), y = Math.min(drag.y0, drag.y1);
    const w = Math.abs(drag.x1 - drag.x0), h = Math.abs(drag.y1 - drag.y0);
    setDrag(null);
    if (w > 0.003 && h > 0.003) props.onDraw?.([+x.toFixed(5), +y.toFixed(5), +w.toFixed(5), +h.toFixed(5)]);
  };
  const live = drag ? [Math.min(drag.x0, drag.x1), Math.min(drag.y0, drag.y1), Math.abs(drag.x1 - drag.x0), Math.abs(drag.y1 - drag.y0)] : props.draft;
  const cursor = props.draw || props.pick ? "crosshair" : "default";

  return (
    <div className={`stage ${props.className ?? ""}`} style={{ aspectRatio: `${props.width} / ${props.height}` }}>
      <div ref={box} className="stage-in" style={{ cursor }} onPointerDown={down} onPointerMove={move} onPointerUp={up}>
        {props.src ? <img src={props.src} alt={props.alt} draggable={false} style={{ objectFit: props.fit ?? "contain" }} /> : <div className="stage-empty">Loading image…</div>}
        <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="stage-svg" aria-hidden>
          {(props.boxes ?? []).map((b) => (
            <rect key={b.id} x={b.bbox[0]} y={b.bbox[1]} width={b.bbox[2]} height={b.bbox[3]} vectorEffect="non-scaling-stroke"
              className={`sbox ${b.tone ?? "info"} ${b.id === props.selectedId ? "sel" : ""} ${b.dashed ? "dash" : ""}`}
              onPointerDown={(e) => { if (!props.draw && !props.pick) { e.stopPropagation(); props.onSelect?.(b.id); } }} />
          ))}
          {live && <rect x={live[0]} y={live[1]} width={live[2]} height={live[3]} vectorEffect="non-scaling-stroke" className="sbox draft" />}
          {props.pickMark && <circle cx={props.pickMark.x} cy={props.pickMark.y} r={0.008} className="sbox sel" vectorEffect="non-scaling-stroke" />}
        </svg>
        {props.children}
      </div>
    </div>
  );
}
