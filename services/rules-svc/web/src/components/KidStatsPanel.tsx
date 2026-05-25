import { Card, CardBody, Spinner } from "@heroui/react";
import { Link } from "@tanstack/react-router";
import { useKidStats } from "../lib/queries";
import type { StatsTopHost } from "../lib/schemas";

export function KidStatsPanel({ name }: { name: string }) {
  const q = useKidStats(name);
  if (q.isLoading) {
    return (
      <Card>
        <CardBody className="flex justify-center py-10">
          <Spinner size="sm" />
        </CardBody>
      </Card>
    );
  }
  if (!q.data) {
    return (
      <Card>
        <CardBody className="text-sm text-default-500 py-4">
          No stats yet.
        </CardBody>
      </Card>
    );
  }
  const s = q.data;
  const has24h = s.top_hosts_24h.length > 0;
  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardBody className="gap-3 p-4">
          <div className="grid grid-cols-2 gap-2">
            <StatTile label="pages 1h" value={s.pages_1h} />
            <StatTile label="reqs 1h" value={s.requests_1h} subdued />
            <StatTile
              label="blocked 1h"
              value={s.blocked_1h}
              danger={s.blocked_1h > 0}
            />
            <StatTile label="pages 24h" value={s.pages_24h} subdued />
          </div>
          {s.sparkline_1h.some((v) => v > 0) && (
            <div>
              <Sparkline values={s.sparkline_1h} />
              <p className="text-[10px] uppercase tracking-wide text-default-400 mt-1 text-center">
                last hour · {s.bucket_secs / 60}m buckets
              </p>
            </div>
          )}
        </CardBody>
      </Card>

      {has24h ? (
        <Card>
          <CardBody className="gap-2 p-4">
            <SectionTitle>Top domains · 24h</SectionTitle>
            <HostList kidName={name} hosts={s.top_hosts_24h} />
          </CardBody>
        </Card>
      ) : (
        <Card>
          <CardBody className="text-sm text-default-500 py-4 text-center">
            No traffic recorded yet — try browsing on a connected device.
          </CardBody>
        </Card>
      )}
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[10px] uppercase tracking-wide text-default-500 font-medium">
      {children}
    </p>
  );
}

function StatTile({
  label,
  value,
  subdued,
  danger,
}: {
  label: string;
  value: number;
  subdued?: boolean;
  danger?: boolean;
}) {
  return (
    <div
      className={
        "rounded-medium px-3 py-2 text-center " +
        (danger
          ? "bg-danger/10 text-danger"
          : subdued
            ? "bg-content2 text-default-500"
            : "bg-content2 text-foreground")
      }
    >
      <div className="text-xl font-semibold leading-tight tabular-nums">
        {formatCount(value)}
      </div>
      <div className="text-[10px] uppercase tracking-wide text-default-500">
        {label}
      </div>
    </div>
  );
}

function HostList({
  kidName,
  hosts,
}: {
  kidName: string;
  hosts: StatsTopHost[];
}) {
  const max = Math.max(1, ...hosts.map((h) => h.requests));
  return (
    <div className="flex flex-col gap-0.5">
      {hosts.map((h) => (
        <Link
          key={h.host}
          to="/kids/$name"
          params={{ name: kidName }}
          search={{ tab: "activity" }}
          className="block hover:bg-content2 rounded-small px-1 py-1 -mx-1"
        >
          <div className="flex items-center justify-between gap-2 text-xs">
            <span className="truncate font-medium">{h.host}</span>
            <span className="flex items-center gap-1.5 shrink-0 tabular-nums text-default-500">
              {h.blocked > 0 && (
                <span className="text-danger font-medium">
                  {h.blocked} blocked
                </span>
              )}
              {h.pages > 0 && (
                <span>{h.pages}p</span>
              )}
              <span className="text-foreground font-medium">
                {formatCount(h.requests)}
              </span>
            </span>
          </div>
          <div className="h-0.5 mt-1 bg-default-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-primary/60"
              style={{ width: `${(h.requests / max) * 100}%` }}
            />
          </div>
        </Link>
      ))}
    </div>
  );
}

function Sparkline({ values }: { values: number[] }) {
  const max = Math.max(1, ...values);
  const w = 100;
  const h = 28;
  const bw = w / values.length;
  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      className="w-full h-8"
      aria-hidden
    >
      {values.map((v, i) => {
        const bh = Math.max(1, (v / max) * h);
        return (
          <rect
            key={i}
            x={i * bw + 0.5}
            y={h - bh}
            width={Math.max(1, bw - 1)}
            height={bh}
            className="fill-primary/70"
          />
        );
      })}
    </svg>
  );
}

function formatCount(n: number): string {
  if (n < 1000) return String(n);
  if (n < 10_000) return (n / 1000).toFixed(1) + "k";
  if (n < 1_000_000) return Math.round(n / 1000) + "k";
  return (n / 1_000_000).toFixed(1) + "M";
}
