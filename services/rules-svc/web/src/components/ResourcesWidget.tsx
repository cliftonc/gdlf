import { Card, CardBody, Skeleton, Tooltip } from "@heroui/react";
import { useResources } from "../lib/queries";
import type { ResourceContainer } from "../lib/schemas";

export function ResourcesWidget() {
  const { data, isLoading, isError } = useResources();

  if (isLoading) {
    return (
      <Card className="w-full">
        <CardBody className="px-3 py-2">
          <Skeleton className="h-8 w-full rounded-medium" />
        </CardBody>
      </Card>
    );
  }

  if (isError || !data || data.containers.length === 0) {
    return null;
  }

  return (
    <Card className="w-full">
      <CardBody className="px-3 py-2 gap-2">
        <span className="text-[10px] uppercase tracking-wider text-default-500">
          Resources
        </span>
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-7 gap-1.5">
          {data.containers.map((c) => (
            <Chip key={c.name} c={c} />
          ))}
        </div>
      </CardBody>
    </Card>
  );
}

function Chip({ c }: { c: ResourceContainer }) {
  const isRunning = c.state === "running";
  const displayName = c.name.replace(/^gdlf-/, "");

  const cpu = c.cpu_percent;
  const cpuText =
    cpu === null || cpu === undefined ? "—" : `${cpu.toFixed(0)}%`;
  const cpuTone = isRunning ? toneFor(cpu ?? 0, 60, 85) : "off";

  const memUsed = c.mem_used_bytes;
  const memLimit = c.mem_limit_bytes;
  const memPct = memLimit > 0 ? (memUsed / memLimit) * 100 : 0;
  const memText = isRunning ? humanBytes(memUsed) : "—";
  const memTone = isRunning ? toneFor(memPct, 70, 90) : "off";

  const tooltip = isRunning
    ? `${c.name} · CPU ${cpuText}${memLimit > 0 ? ` · ${humanBytes(memUsed)} / ${humanBytes(memLimit)}` : ` · ${humanBytes(memUsed)}`}`
    : `${c.name} · ${c.state}`;

  return (
    <Tooltip content={tooltip} placement="top" delay={300}>
      <span className="flex items-center justify-between gap-1.5 rounded-md border border-default-200 bg-content2/50 px-2.5 py-1.5 text-xs leading-tight min-w-0">
        <span className="flex items-center gap-1.5 min-w-0">
          <span
            className={`h-1.5 w-1.5 rounded-full shrink-0 ${
              isRunning ? "bg-success" : "bg-default-300"
            }`}
            aria-label={isRunning ? "running" : c.state}
          />
          <span className="font-medium truncate">{displayName}</span>
        </span>
        <span className="flex items-center gap-1.5 shrink-0">
          <span className={`tabular-nums ${toneClass(cpuTone)}`}>{cpuText}</span>
          <span className="text-default-300">·</span>
          <span className={`tabular-nums ${toneClass(memTone)}`}>{memText}</span>
        </span>
      </span>
    </Tooltip>
  );
}

type Tone = "ok" | "warn" | "hot" | "off";

function toneFor(v: number, warn: number, hot: number): Tone {
  if (v >= hot) return "hot";
  if (v >= warn) return "warn";
  return "ok";
}

function toneClass(tone: Tone): string {
  switch (tone) {
    case "hot":
      return "text-danger";
    case "warn":
      return "text-warning";
    case "off":
      return "text-default-400";
    default:
      return "text-default-600";
  }
}

function humanBytes(n: number): string {
  if (!n || n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  const digits = v >= 100 || i === 0 ? 0 : v >= 10 ? 1 : 2;
  return `${v.toFixed(digits)}${units[i]}`;
}
