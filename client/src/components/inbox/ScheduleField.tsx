"use client";

import { useId } from "react";

const MAX_DAYS = 90;

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

/** Local `YYYY-MM-DDTHH:mm` for a datetime-local input. */
function toLocalInput(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** RFC 3339 string with the viewer's UTC offset, as the API requires. */
export function toOffsetIso(localValue: string): string | null {
  const date = new Date(localValue);
  if (Number.isNaN(date.getTime())) return null;
  const offset = -date.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const hours = pad(Math.floor(Math.abs(offset) / 60));
  const minutes = pad(Math.abs(offset) % 60);
  return `${toLocalInput(date)}:00${sign}${hours}:${minutes}`;
}

/** Returns a message when the chosen time cannot be used, or an empty string. */
export function scheduleProblem(localValue: string): string {
  if (!localValue) return "";
  const date = new Date(localValue);
  if (Number.isNaN(date.getTime())) return "Choose a valid date and time.";
  const now = Date.now();
  if (date.getTime() <= now) return "Choose a time in the future.";
  if (date.getTime() > now + MAX_DAYS * 24 * 3600 * 1000) return `Scheduled sends can be at most ${MAX_DAYS} days ahead.`;
  return "";
}

export function ScheduleField({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const id = useId();
  const problem = scheduleProblem(value);
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-[10px] text-[#8b949e]">
        Send later (optional, {zone})
      </label>
      <div className="flex items-center gap-2">
        <input
          id={id}
          type="datetime-local"
          value={value}
          min={toLocalInput(new Date())}
          onChange={(event) => onChange(event.target.value)}
          aria-invalid={Boolean(problem)}
          aria-describedby={`${id}-help`}
          className="rounded border border-[#30363d] bg-[#0d1117] p-1.5 text-xs text-[#e6edf3] focus:outline-none focus:border-zinc-500"
        />
        {value && (
          <button type="button" onClick={() => onChange("")} className="text-[11px] text-[#8b949e] hover:text-[#e6edf3]">
            Send now instead
          </button>
        )}
      </div>
      <p id={`${id}-help`} role={problem ? "alert" : undefined} className={`text-[10px] ${problem ? "text-red-400" : "text-[#8b949e]"}`}>
        {problem || (value ? "Moving the message to trash before then cancels it." : "Leave empty to send right away.")}
      </p>
    </div>
  );
}
