import { Card, CardBody, Chip, Switch } from "@heroui/react";
import { Link } from "@tanstack/react-router";
import type { KidStats, KidSummary, KidSummaryDevice } from "../lib/schemas";
import { useKidBlock, useDeviceBlock } from "../lib/mutations";
import { useConfirm } from "../lib/hooks/useConfirm";
import { PlatformIcon } from "./PlatformIcon";

export function KidCard({
  kid,
  stats,
}: {
  kid: KidSummary;
  stats?: KidStats;
}) {
  const onBonus =
    kid.bonus_until && new Date(kid.bonus_until).getTime() > Date.now();
  const blockKid = useKidBlock(kid.name);
  const confirm = useConfirm();

  const onBlockToggle = async (next: boolean) => {
    if (next === kid.manual_block) return;
    const ok = await confirm({
      title: next ? `Block ${kid.name}?` : `Unblock ${kid.name}?`,
      body: next
        ? "Every device for this kid will be cut off until you unblock."
        : "Network access resumes immediately, subject to schedule.",
      confirmLabel: next ? "Block" : "Unblock",
      danger: next,
    });
    if (!ok) return;
    blockKid.mutate(next);
  };

  return (
    <Card className="w-full h-full">
      <CardBody className="gap-3 p-4">
        <div className="flex items-start justify-between gap-3">
          <Link
            to="/kids/$name"
            params={{ name: kid.name }}
            className="flex-1 min-w-0 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded-medium"
          >
            <div className="flex items-center gap-2 flex-wrap">
              <p className="text-lg font-semibold leading-tight truncate hover:underline">
                {kid.name}
              </p>
              {onBonus && (
                <Chip color="warning" size="sm" variant="flat">
                  Bonus
                </Chip>
              )}
            </div>
            <p className="text-xs text-default-500 mt-0.5 flex items-center gap-2">
              {kid.age !== null && <span>Age {kid.age}</span>}
              <span>
                <strong className="text-foreground">
                  {kid.online_device_count}
                </strong>
                /{kid.device_count} online
              </span>
            </p>
          </Link>
          <Switch
            isSelected={kid.manual_block}
            onValueChange={onBlockToggle}
            color="danger"
            size="sm"
            aria-label={kid.manual_block ? "Unblock kid" : "Block kid"}
          >
            <span className="text-xs">Block</span>
          </Switch>
        </div>

        <StatStrip stats={stats} />

        {stats && stats.sparkline_1h.some((v) => v > 0) && (
          <Sparkline values={stats.sparkline_1h} />
        )}

        {stats && stats.top_hosts_1h.length > 0 && (
          <TopHosts kidName={kid.name} hosts={stats.top_hosts_1h} />
        )}

        {kid.devices.length > 0 && (
          <DeviceList kidName={kid.name} devices={kid.devices} />
        )}

        <Link
          to="/kids/$name"
          params={{ name: kid.name }}
          search={{ tab: "schedule" }}
          className="text-xs text-default-400 truncate hover:text-default-600 hover:underline"
        >
          wd {kid.schedule.weekday} · we {kid.schedule.weekend}
        </Link>
      </CardBody>
    </Card>
  );
}

function StatStrip({ stats }: { stats?: KidStats }) {
  const pages = stats?.pages_1h ?? 0;
  const requests = stats?.requests_1h ?? 0;
  const blocked = stats?.blocked_1h ?? 0;
  return (
    <div className="grid grid-cols-3 gap-2">
      <StatTile label="pages 1h" value={pages} />
      <StatTile label="reqs 1h" value={requests} subdued />
      <StatTile label="blocked" value={blocked} danger={blocked > 0} />
    </div>
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
        "rounded-medium px-2 py-1.5 text-center " +
        (danger
          ? "bg-danger/10 text-danger"
          : subdued
            ? "bg-content2 text-default-500"
            : "bg-content2 text-foreground")
      }
    >
      <div className="text-base font-semibold leading-tight tabular-nums">
        {formatCount(value)}
      </div>
      <div className="text-[10px] uppercase tracking-wide text-default-500">
        {label}
      </div>
    </div>
  );
}

function formatCount(n: number): string {
  if (n < 1000) return String(n);
  if (n < 10_000) return (n / 1000).toFixed(1) + "k";
  if (n < 1_000_000) return Math.round(n / 1000) + "k";
  return (n / 1_000_000).toFixed(1) + "M";
}

function Sparkline({ values }: { values: number[] }) {
  const max = Math.max(1, ...values);
  const w = 100;
  const h = 18;
  const bw = w / values.length;
  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      className="w-full h-5"
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

function TopHosts({
  kidName,
  hosts,
}: {
  kidName: string;
  hosts: KidStats["top_hosts_1h"];
}) {
  return (
    <div className="flex flex-col gap-0.5 text-xs">
      {hosts.map((h) => (
        <Link
          key={h.host}
          to="/kids/$name"
          params={{ name: kidName }}
          search={{ tab: "activity" }}
          className="flex items-center justify-between gap-2 hover:bg-content2 rounded-small px-1 py-0.5 -mx-1"
        >
          <span className="truncate font-medium">{h.host}</span>
          <span className="flex items-center gap-1.5 shrink-0 tabular-nums text-default-500">
            {h.blocked > 0 && (
              <span className="text-danger font-medium">{h.blocked}!</span>
            )}
            <span>{formatCount(h.requests)}</span>
          </span>
        </Link>
      ))}
    </div>
  );
}

function DeviceList({
  kidName,
  devices,
}: {
  kidName: string;
  devices: KidSummaryDevice[];
}) {
  const block = useDeviceBlock(kidName);
  const confirm = useConfirm();

  const toggle = async (d: KidSummaryDevice) => {
    const next = !d.manual_block;
    const ok = await confirm({
      title: next ? `Block ${d.name}?` : `Unblock ${d.name}?`,
      body: next
        ? `${d.name} (${d.wg_ip}) will be cut off until you unblock.`
        : `${d.name} resumes network access immediately.`,
      confirmLabel: next ? "Block" : "Unblock",
      danger: next,
    });
    if (!ok) return;
    block.mutate({ ip: d.wg_ip, blocked: next });
  };

  return (
    <div className="flex flex-col gap-1 border-t border-default-200 pt-2">
      {devices.map((d) => (
        <div
          key={d.wg_ip}
          className="flex items-center gap-2 text-sm"
        >
          <PlatformIcon platform={d.platform} className="text-base shrink-0 text-default-500" />
          <span
            className={
              "w-1.5 h-1.5 rounded-full shrink-0 " +
              (d.manual_block
                ? "bg-danger"
                : d.online
                  ? "bg-success"
                  : "bg-default-400")
            }
            aria-label={d.online ? "online" : "offline"}
            title={d.online ? "online" : "offline"}
          />
          <span className="truncate flex-1 min-w-0">{d.name}</span>
          <Switch
            size="sm"
            isSelected={!d.manual_block}
            onValueChange={() => toggle(d)}
            color="success"
            aria-label={d.manual_block ? `Unblock ${d.name}` : `Block ${d.name}`}
          />
        </div>
      ))}
    </div>
  );
}
