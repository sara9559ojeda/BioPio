import { useMemo, useRef, useState } from "react";
import { fmtNum, fmtShortDate } from "../format";

export interface ChartSeries {
  name: string;
  // CSS variable with the series color (e.g. "var(--series-1)").
  color: string;
  points: { x: Date; y: number }[];
}

interface Props {
  series: ChartSeries[];
  unit: string;
  height?: number;
  marker?: { x: Date; label: string };
  ariaLabel: string;
}

const WIDTH = 720;
const PAD = { top: 16, right: 16, bottom: 28, left: 48 };

function niceMax(value: number): number {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].find((s) => s * magnitude >= value / 4) ?? 10;
  return Math.ceil(value / (step * magnitude)) * step * magnitude;
}

export function LineChart({ series, unit, height = 240, marker, ariaLabel }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);

  const geometry = useMemo(() => {
    const all = series.flatMap((s) => s.points);
    if (!all.length) return null;
    const xs = all.map((p) => p.x.getTime());
    const xMin = Math.min(...xs);
    const xMax = Math.max(...xs);
    const yMin = Math.min(0, ...all.map((p) => p.y));
    const yMax = niceMax(Math.max(...all.map((p) => p.y)));
    const innerW = WIDTH - PAD.left - PAD.right;
    const innerH = height - PAD.top - PAD.bottom;
    const sx = (t: number) => PAD.left + ((t - xMin) / Math.max(xMax - xMin, 1)) * innerW;
    const sy = (v: number) => PAD.top + innerH - ((v - yMin) / Math.max(yMax - yMin, 1e-9)) * innerH;
    const xTimes = [...new Set(xs)].sort((a, b) => a - b);
    return { xMin, xMax, yMin, yMax, sx, sy, innerW, innerH, xTimes };
  }, [series, height]);

  if (!geometry) return <div className="chart-empty muted">Sin datos para graficar.</div>;
  const { sx, sy, yMin, yMax, xTimes } = geometry;
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => yMin + (yMax - yMin) * f);
  const xTicks = xTimes.filter((_, i) => i % Math.max(1, Math.ceil(xTimes.length / 6)) === 0);
  const hoverTime = hover !== null ? xTimes[hover] : null;

  function onMove(event: React.PointerEvent<SVGSVGElement>) {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * WIDTH;
    let best = 0;
    xTimes.forEach((t, i) => {
      if (Math.abs(sx(t) - x) < Math.abs(sx(xTimes[best]) - x)) best = i;
    });
    setHover(best);
  }

  const tooltipLeft = hoverTime !== null ? (sx(hoverTime) / WIDTH) * 100 : 0;

  return (
    <figure className="chart" aria-label={ariaLabel}>
      {series.length > 1 && (
        <figcaption className="legend">
          {series.map((s) => (
            <span key={s.name} className="legend__item">
              <span className="legend__key" style={{ background: s.color }} />
              {s.name}
            </span>
          ))}
        </figcaption>
      )}
      <div className="chart__plot">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${WIDTH} ${height}`}
          role="img"
          aria-label={ariaLabel}
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
        >
          {yTicks.map((v) => (
            <g key={v}>
              <line className="chart__grid" x1={PAD.left} x2={WIDTH - PAD.right} y1={sy(v)} y2={sy(v)} />
              <text className="chart__tick" x={PAD.left - 8} y={sy(v) + 4} textAnchor="end">
                {fmtNum(v, v < 10 && v !== 0 ? 1 : 0)}
              </text>
            </g>
          ))}
          {xTicks.map((t) => (
            <text key={t} className="chart__tick" x={sx(t)} y={height - 8} textAnchor="middle">
              {fmtShortDate(new Date(t))}
            </text>
          ))}
          {marker && (
            <g>
              <line className="chart__marker" x1={sx(marker.x.getTime())} x2={sx(marker.x.getTime())} y1={PAD.top} y2={height - PAD.bottom} />
              <text className="chart__marker-label" x={sx(marker.x.getTime()) + 6} y={PAD.top + 10}>
                {marker.label}
              </text>
            </g>
          )}
          {series.map((s) => (
            <path
              key={s.name}
              className="chart__line"
              style={{ stroke: s.color }}
              d={s.points.map((p, i) => `${i ? "L" : "M"}${sx(p.x.getTime()).toFixed(1)},${sy(p.y).toFixed(1)}`).join(" ")}
            />
          ))}
          {hoverTime !== null && (
            <g>
              <line className="chart__crosshair" x1={sx(hoverTime)} x2={sx(hoverTime)} y1={PAD.top} y2={height - PAD.bottom} />
              {series.map((s) => {
                const p = s.points.find((pt) => pt.x.getTime() === hoverTime);
                return p ? <circle key={s.name} className="chart__dot" cx={sx(hoverTime)} cy={sy(p.y)} r={4.5} style={{ fill: s.color }} /> : null;
              })}
            </g>
          )}
        </svg>
        {hoverTime !== null && (
          <div className="chart__tooltip" style={{ left: `${tooltipLeft}%` }} role="status">
            <strong>{fmtShortDate(new Date(hoverTime))}</strong>
            {series.map((s) => {
              const p = s.points.find((pt) => pt.x.getTime() === hoverTime);
              return p ? (
                <span key={s.name} className="chart__tooltip-row">
                  <span className="legend__key" style={{ background: s.color }} />
                  {s.name}: <b>{fmtNum(p.y, 2)}</b> {unit}
                </span>
              ) : null;
            })}
          </div>
        )}
      </div>
    </figure>
  );
}
