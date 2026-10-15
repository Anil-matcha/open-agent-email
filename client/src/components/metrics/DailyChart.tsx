"use client";

import { useId, useState } from "react";
import type { AccountMetricsBucket } from "@/lib/types";
import { count } from "@/lib/metrics-api";

// Two series, so two fixed categorical slots (blue, orange), validated against this app's
// dark surface. Identity never rests on colour alone: the legend names each series, the
// bars sit in a fixed left/right order, and the table view repeats every number.
const SERIES = [
  { key: "received", label: "Received", color: "#3987e5" },
  { key: "delivered", label: "Delivered", color: "#d95926" },
] as const;

type SeriesKey = (typeof SERIES)[number]["key"];

const WIDTH = 720;
const HEIGHT = 240;
const MARGIN = { top: 12, right: 12, bottom: 28, left: 40 };

function niceMax(value: number): number {
  if (value <= 4) return 4;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 5, 10].map((m) => m * magnitude).find((candidate) => candidate >= value / 4) ?? magnitude;
  return Math.ceil(value / step) * step;
}

function dayLabel(day: string): string {
  const parsed = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return day;
  return parsed.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
}

export function DailyChart({ buckets }: { buckets: AccountMetricsBucket[] }) {
  const [view, setView] = useState<"chart" | "table">("chart");
  const [hover, setHover] = useState<{ index: number; key: SeriesKey } | null>(null);
  const titleId = useId();

  const days = [...buckets].sort((a, b) => a.day.localeCompare(b.day));
  const value = (bucket: AccountMetricsBucket, key: SeriesKey) => bucket.summary[key];
  const max = niceMax(Math.max(0, ...days.flatMap((bucket) => SERIES.map((s) => value(bucket, s.key)))));
  const plotWidth = WIDTH - MARGIN.left - MARGIN.right;
  const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom;
  const band = days.length ? plotWidth / days.length : plotWidth;
  const barWidth = Math.max(4, Math.min(22, band / 2 - 6));
  const y = (v: number) => MARGIN.top + plotHeight - (v / max) * plotHeight;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((fraction) => Math.round(max * fraction));
  // Label roughly six days along the axis so long ranges stay readable.
  const labelEvery = Math.max(1, Math.ceil(days.length / 6));
  const total = (key: SeriesKey) => days.reduce((sum, bucket) => sum + value(bucket, key), 0);
  const hovered = hover ? { bucket: days[hover.index], series: SERIES.find((s) => s.key === hover.key)! } : null;

  return (
    <section aria-labelledby={titleId} className="rounded-lg border border-[#30363d] bg-[#161b22] p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id={titleId} className="text-sm font-semibold text-[#e6edf3]">Messages per day</h2>
          <p className="text-[11px] text-[#8b949e]">Mail received and mail delivered, by UTC day.</p>
        </div>
        <div className="flex items-center gap-4">
          <ul className="flex items-center gap-4 text-[11px] text-[#c9d1d9]" aria-label="Legend">
            {SERIES.map((series) => (
              <li key={series.key} className="flex items-center gap-1.5">
                <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: series.color }} aria-hidden="true" />
                {series.label} <span className="font-mono text-[#8b949e]">{count(total(series.key))}</span>
              </li>
            ))}
          </ul>
          <div role="group" aria-label="Chart or table" className="flex overflow-hidden rounded-md border border-[#30363d] text-[11px]">
            {(["chart", "table"] as const).map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={view === option}
                onClick={() => setView(option)}
                className={`px-2.5 py-1 capitalize ${view === option ? "bg-[#21262d] text-white" : "text-[#8b949e] hover:text-[#e6edf3]"}`}
              >
                {option}
              </button>
            ))}
          </div>
        </div>
      </div>

      {days.length === 0 ? (
        <p className="mt-6 mb-4 text-center text-xs text-[#8b949e]">No messages in this range.</p>
      ) : view === "table" ? (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-xs">
            <caption className="sr-only">Messages received and delivered per day</caption>
            <thead className="text-[#8b949e]">
              <tr>
                <th scope="col" className="py-1.5 pr-4 font-medium">Day (UTC)</th>
                {SERIES.map((series) => (
                  <th key={series.key} scope="col" className="py-1.5 pr-4 text-right font-medium">{series.label}</th>
                ))}
              </tr>
            </thead>
            <tbody className="font-mono text-[#e6edf3]">
              {days.map((bucket) => (
                <tr key={bucket.day} className="border-t border-[#21262d]">
                  <th scope="row" className="py-1.5 pr-4 text-left font-normal text-[#c9d1d9]">{bucket.day}</th>
                  {SERIES.map((series) => (
                    <td key={series.key} className="py-1.5 pr-4 text-right">{count(value(bucket, series.key))}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="relative mt-3">
          <svg
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            role="img"
            aria-label={`Bar chart of messages received and delivered per day. Use the Table view for exact numbers.`}
            className="h-auto w-full"
            onMouseLeave={() => setHover(null)}
          >
            {ticks.map((tick) => (
              <g key={tick}>
                <line x1={MARGIN.left} x2={WIDTH - MARGIN.right} y1={y(tick)} y2={y(tick)} stroke="#21262d" strokeWidth={1} />
                <text x={MARGIN.left - 6} y={y(tick) + 3} textAnchor="end" fontSize={10} fill="#8b949e">{tick}</text>
              </g>
            ))}
            {days.map((bucket, index) => {
              const center = MARGIN.left + band * index + band / 2;
              return (
                <g key={bucket.day}>
                  {SERIES.map((series, seriesIndex) => {
                    const v = value(bucket, series.key);
                    const height = Math.max(v > 0 ? 2 : 0, plotHeight - (y(v) - MARGIN.top));
                    const x = center + (seriesIndex === 0 ? -barWidth - 1 : 1);
                    const top = MARGIN.top + plotHeight - height;
                    return (
                      <g key={series.key}>
                        {/* Wider invisible target than the visible bar. */}
                        <rect
                          x={x - 3}
                          y={MARGIN.top}
                          width={barWidth + 6}
                          height={plotHeight}
                          fill="transparent"
                          onMouseEnter={() => setHover({ index, key: series.key })}
                        />
                        <rect
                          x={x}
                          y={top}
                          width={barWidth}
                          height={height}
                          rx={2}
                          fill={series.color}
                          opacity={hover && (hover.index !== index || hover.key !== series.key) ? 0.55 : 1}
                          pointerEvents="none"
                        />
                      </g>
                    );
                  })}
                  {index % labelEvery === 0 && (
                    <text x={center} y={HEIGHT - 8} textAnchor="middle" fontSize={10} fill="#8b949e">{dayLabel(bucket.day)}</text>
                  )}
                </g>
              );
            })}
            <line x1={MARGIN.left} x2={WIDTH - MARGIN.right} y1={y(0)} y2={y(0)} stroke="#30363d" strokeWidth={1} />
          </svg>
          {hovered && (
            <div
              role="status"
              className="pointer-events-none absolute right-2 top-0 rounded-md border border-[#30363d] bg-[#0d1117] px-2.5 py-1.5 text-[11px] text-[#e6edf3] shadow-lg"
            >
              <div className="text-[#8b949e]">{hovered.bucket.day}</div>
              <div className="flex items-center gap-1.5">
                <span className="inline-block h-2 w-2 rounded-sm" style={{ background: hovered.series.color }} aria-hidden="true" />
                {hovered.series.label}: <span className="font-mono">{count(value(hovered.bucket, hovered.series.key))}</span>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
