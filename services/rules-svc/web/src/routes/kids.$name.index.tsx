import { useMemo, useState } from "react";
import {
  createFileRoute,
  Link,
  useNavigate,
  useSearch,
} from "@tanstack/react-router";
import {
  Accordion,
  AccordionItem,
  Button,
  Chip,
  Input,
  Spinner,
  Switch,
  Tab,
  Tabs,
} from "@heroui/react";
import { z } from "zod";
import {
  useKid,
  useActivity,
  useServices,
  useTlsFailures,
} from "../lib/queries";
import {
  useDeleteKid,
  useKidBlock,
  useAddPassthrough,
  useRemovePassthrough,
  useSetBlockedApps,
  useSetPassthrough,
  useDismissTlsFailure,
} from "../lib/mutations";
import type { Service, TlsFailureGroup } from "../lib/schemas";
import { useConfirm } from "../lib/hooks/useConfirm";
import { DeviceRow } from "../components/DeviceRow";
import { RuleRow } from "../components/RuleRow";
import { ScheduleEditor } from "../components/ScheduleEditor";
import { BonusControls } from "../components/BonusControls";
import { ActivityTable } from "../components/ActivityTable";
import { EmptyState } from "../components/EmptyState";

const TabEnum = z.enum([
  "devices",
  "schedule",
  "rules",
  "services",
  "passthrough",
  "activity",
]);

export const Route = createFileRoute("/kids/$name/")({
  validateSearch: z.object({ tab: TabEnum.optional() }),
  component: KidDetailPage,
});

function KidDetailPage() {
  const { name } = Route.useParams();
  const { tab = "devices" } = useSearch({ from: "/kids/$name/" });
  const nav = useNavigate();
  const kid = useKid(name);
  const del = useDeleteKid();
  const block = useKidBlock(name);
  const confirm = useConfirm();
  const [busy, setBusy] = useState(false);

  const onDelete = async () => {
    const ok = await confirm({
      title: `Remove ${name}?`,
      body: "All their devices, rules, and keys are deleted.",
      confirmLabel: "Remove kid",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await del.mutateAsync(name);
      nav({ to: "/kids" });
    } finally {
      setBusy(false);
    }
  };

  if (kid.isLoading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner />
      </div>
    );
  }
  if (kid.error || !kid.data) {
    return <p className="text-danger">Failed to load: {String(kid.error)}</p>;
  }
  const k = kid.data;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <Link to="/kids" className="text-sm text-default-500 hover:underline">
            ← Kids
          </Link>
          <h1 className="text-2xl font-semibold">{k.name}</h1>
          {k.age !== null && (
            <p className="text-sm text-default-500">Age {k.age}</p>
          )}
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <Switch
            isSelected={k.manual_block}
            onValueChange={(v) => block.mutate(v)}
            color="danger"
          >
            Block all devices
          </Switch>
          <Button
            color="danger"
            variant="light"
            onPress={onDelete}
            isDisabled={busy}
          >
            Remove kid
          </Button>
        </div>
      </div>

      <Tabs
        selectedKey={tab}
        onSelectionChange={(key) =>
          nav({
            to: "/kids/$name",
            params: { name },
            search: { tab: key as z.infer<typeof TabEnum> },
            replace: true,
          })
        }
        variant="underlined"
        classNames={{ tabList: "overflow-x-auto" }}
      >
        <Tab key="devices" title="Devices">
          <DevicesTab name={name} devices={k.devices} />
        </Tab>
        <Tab key="schedule" title="Schedule">
          <div className="flex flex-col gap-6 max-w-xl">
            <ScheduleEditor name={name} schedule={k.schedule} />
            <BonusControls name={name} bonusUntil={k.bonus_until} />
          </div>
        </Tab>
        <Tab key="rules" title={`Rules (${k.rules.length})`}>
          <RulesTab name={name} />
        </Tab>
        <Tab
          key="services"
          title={`Services (${k.blocked_apps.length})`}
        >
          <ServicesTab name={name} blockedApps={k.blocked_apps} />
        </Tab>
        <Tab
          key="passthrough"
          title={`Passthrough (${k.mitm_passthrough_hosts.length})`}
        >
          <PassthroughTab name={name} hosts={k.mitm_passthrough_hosts} />
        </Tab>
        <Tab key="activity" title="Activity">
          <KidActivityTab name={name} />
        </Tab>
      </Tabs>
    </div>
  );
}

function DevicesTab({ name, devices }: { name: string; devices: import("../lib/schemas").Device[] }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex justify-end">
        <Button
          as={Link}
          to="/kids/$name/devices/new"
          params={{ name }}
          color="primary"
        >
          Add device
        </Button>
      </div>
      {devices.length === 0 ? (
        <EmptyState
          title="No devices yet"
          body="Enrol a phone, tablet, or laptop to start filtering its traffic."
        />
      ) : (
        devices.map((d) => <DeviceRow key={d.wg_ip} kidName={name} device={d} />)
      )}
    </div>
  );
}

function RulesTab({ name }: { name: string }) {
  const kid = useKid(name);
  if (!kid.data) return null;
  const rules = kid.data.rules;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex justify-end">
        <Button
          as={Link}
          to="/kids/$name/rules/new"
          params={{ name }}
          color="primary"
        >
          Add rule
        </Button>
      </div>
      {rules.length === 0 ? (
        <EmptyState
          title="No rules yet"
          body="Rules are evaluated top-to-bottom; first match wins."
        />
      ) : (
        rules.map((r, i) => (
          <RuleRow key={i} kidName={name} rule={r} idx={i} total={rules.length} />
        ))
      )}
    </div>
  );
}

// A registrable domain `reg` is "enabled for passthrough" iff at least one
// of its canonical glob forms is present in the kid's passthrough list.
function groupPatterns(registrable: string): [string, string] {
  return [registrable, `*.${registrable}`];
}

function isGroupEnabled(registrable: string, hosts: string[]): boolean {
  const [a, b] = groupPatterns(registrable);
  return hosts.includes(a) || hosts.includes(b);
}

// "Custom" host = an entry in the kid's passthrough list that isn't the
// canonical apex / `*.<reg>` form of any observed group. These are the
// rules the parent typed by hand and we don't want to clobber.
function customHosts(hosts: string[], groups: TlsFailureGroup[]): string[] {
  const known = new Set<string>();
  for (const g of groups) {
    const [a, b] = groupPatterns(g.registrable);
    known.add(a);
    known.add(b);
  }
  return hosts.filter((h) => !known.has(h));
}

function formatRelative(iso: string | null): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  const delta = Math.max(0, Date.now() - t);
  const m = Math.floor(delta / 60_000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function PassthroughTab({
  name,
  hosts,
}: {
  name: string;
  hosts: string[];
}) {
  const failures = useTlsFailures(name);
  const setHosts = useSetPassthrough(name);
  const addHost = useAddPassthrough(name);
  const removeHost = useRemovePassthrough(name);
  const dismiss = useDismissTlsFailure();
  const [custom, setCustom] = useState("");

  const groups = failures.data ?? [];
  const manual = useMemo(() => customHosts(hosts, groups), [hosts, groups]);

  const toggleGroup = (registrable: string, enable: boolean) => {
    const [a, b] = groupPatterns(registrable);
    const next = enable
      ? Array.from(new Set([...hosts, a, b])).sort()
      : hosts.filter((h) => h !== a && h !== b);
    setHosts.mutate(next);
  };

  const submitCustom = () => {
    const v = custom.trim().toLowerCase();
    if (!v) return;
    addHost.mutate(v, { onSuccess: () => setCustom("") });
  };

  return (
    <div className="flex flex-col gap-6 max-w-2xl">
      <div className="text-sm text-default-500 leading-relaxed">
        When an app refuses mitmproxy's certificate (pinned-cert apps like
        TikTok, Instagram, banking) we capture the host here. Toggle a group
        on to let mitmproxy pass that domain through untouched — DNS-level
        blocking still applies, but we lose URL-level visibility for it.
      </div>

      <section className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-default-500">
          Observed pinned domains
        </h2>
        {failures.isLoading && <Spinner size="sm" />}
        {!failures.isLoading && groups.length === 0 && (
          <EmptyState
            title="No TLS failures observed yet"
            body="When a pinned-cert app fails, it'll appear here grouped by domain."
          />
        )}
        {groups.length > 0 && (
          <Accordion variant="bordered" selectionMode="multiple" isCompact>
            {groups.map((g) => {
              const enabled = isGroupEnabled(g.registrable, hosts);
              return (
                <AccordionItem
                  key={g.registrable}
                  aria-label={g.registrable}
                  title={
                    <div className="flex items-center justify-between gap-3 pr-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="font-mono text-sm truncate">
                          {g.registrable}
                        </span>
                        <Chip size="sm" variant="flat" color="default">
                          {g.children.length}
                          {g.children.length === 1 ? " host" : " hosts"}
                        </Chip>
                        <span className="text-xs text-default-400 whitespace-nowrap">
                          {formatRelative(g.ts_last)}
                        </span>
                      </div>
                      <div onClick={(e) => e.stopPropagation()}>
                        <Switch
                          size="sm"
                          isSelected={enabled}
                          onValueChange={(v) =>
                            toggleGroup(g.registrable, v)
                          }
                          isDisabled={setHosts.isPending}
                          color="primary"
                        >
                          {enabled ? "Allowed" : "Allow"}
                        </Switch>
                      </div>
                    </div>
                  }
                >
                  <ul className="flex flex-col gap-1 pl-1">
                    {g.children.map((c) => (
                      <li
                        key={`${c.id}-${c.host}`}
                        className="flex items-center justify-between gap-2 py-1"
                      >
                        <div className="flex flex-col min-w-0">
                          <span className="font-mono text-xs truncate">
                            {c.host}
                          </span>
                          <span className="text-[11px] text-default-400">
                            {c.count}× · last {formatRelative(c.ts_last)}
                            {c.device && ` · ${c.device}`}
                          </span>
                        </div>
                        {c.id != null && (
                          <Button
                            size="sm"
                            variant="light"
                            color="default"
                            isLoading={
                              dismiss.isPending && dismiss.variables === c.id
                            }
                            onPress={() => dismiss.mutate(c.id as number)}
                          >
                            Dismiss
                          </Button>
                        )}
                      </li>
                    ))}
                  </ul>
                </AccordionItem>
              );
            })}
          </Accordion>
        )}
      </section>

      <section className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-default-500">
          Custom rules
        </h2>
        <p className="text-xs text-default-400">
          fnmatch globs for hosts you want to allow before they ever fail —
          e.g. <span className="font-mono">*.bank.example</span>.
        </p>
        <div className="flex gap-2">
          <Input
            size="sm"
            placeholder="e.g. *.tiktok.com"
            value={custom}
            onValueChange={setCustom}
            onKeyDown={(e) => {
              if (e.key === "Enter") submitCustom();
            }}
            className="flex-1"
          />
          <Button
            color="primary"
            onPress={submitCustom}
            isLoading={addHost.isPending}
            isDisabled={!custom.trim()}
          >
            Add
          </Button>
        </div>
        {manual.length === 0 ? (
          <p className="text-xs text-default-400 italic">No custom rules.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {manual.map((h) => (
              <li
                key={h}
                className="flex items-center justify-between gap-2 rounded-md border border-default-200 px-3 py-2"
              >
                <span className="font-mono text-sm">{h}</span>
                <Button
                  size="sm"
                  variant="light"
                  color="danger"
                  isLoading={
                    removeHost.isPending && removeHost.variables === h
                  }
                  onPress={() => removeHost.mutate(h)}
                >
                  Remove
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function humanizeGroupId(gid: string): string {
  if (!gid) return "";
  // AdGuard group ids are lower_snake (e.g. "social_network", "video_streaming").
  return gid
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function ServicesTab({
  name,
  blockedApps,
}: {
  name: string;
  blockedApps: string[];
}) {
  const catalog = useServices();
  const setApps = useSetBlockedApps(name);
  const [filter, setFilter] = useState("");

  // The current toggle state in the YAML may include service ids no longer in
  // the AdGuard catalog (renamed / removed upstream). Preserve them as
  // "unknown" so a toggle-off still works.
  const selected = useMemo(() => new Set(blockedApps), [blockedApps]);
  const toggle = (id: string, enable: boolean) => {
    const next = new Set(selected);
    if (enable) next.add(id);
    else next.delete(id);
    setApps.mutate(Array.from(next).sort());
  };

  const groupsById = useMemo(() => {
    const out: Record<string, string | undefined> = {};
    for (const g of catalog.data?.groups ?? []) out[g.id] = g.name;
    return out;
  }, [catalog.data]);

  const grouped = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const acc: Record<string, Service[]> = {};
    for (const s of catalog.data?.services ?? []) {
      if (q && !s.name.toLowerCase().includes(q) && !s.id.toLowerCase().includes(q)) {
        continue;
      }
      const key = s.group_id || "other";
      (acc[key] ||= []).push(s);
    }
    return Object.entries(acc)
      .map(([gid, services]) => ({
        gid,
        name: groupsById[gid] || humanizeGroupId(gid) || "Other",
        services: services.sort((a, b) => a.name.localeCompare(b.name)),
      }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [catalog.data, filter, groupsById]);

  const unknownSelected = useMemo(() => {
    const known = new Set((catalog.data?.services ?? []).map((s) => s.id));
    return blockedApps.filter((id) => !known.has(id));
  }, [blockedApps, catalog.data]);

  const groupAction = (services: Service[], block: boolean) => {
    const ids = services.map((s) => s.id);
    const next = new Set(selected);
    for (const id of ids) {
      if (block) next.add(id);
      else next.delete(id);
    }
    setApps.mutate(Array.from(next).sort());
  };

  if (catalog.isLoading) {
    return (
      <div className="flex justify-center py-10">
        <Spinner />
      </div>
    );
  }
  if (catalog.error) {
    return (
      <EmptyState
        title="Couldn't load AdGuard services"
        body="rules-svc couldn't reach AdGuard. Is it running and reachable?"
      />
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="text-sm text-default-500 leading-relaxed max-w-2xl">
        Shortcut to AdGuard's per-client blocked services. Toggles here apply
        only to {name}'s devices — they're pushed into each AdGuard client's
        per-client <code className="font-mono">blocked_services</code> array
        on the next sync (within ~60s).
      </div>

      <Input
        size="sm"
        placeholder="Filter services…"
        value={filter}
        onValueChange={setFilter}
        className="max-w-sm"
      />

      {unknownSelected.length > 0 && (
        <section className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-default-500">
            Unknown to AdGuard
          </h2>
          <p className="text-xs text-default-400">
            Persisted in kids.yaml but not in the current catalog. Likely
            renamed or removed upstream.
          </p>
          <div className="flex flex-wrap gap-2">
            {unknownSelected.map((id) => (
              <Chip
                key={id}
                onClose={() => toggle(id, false)}
                variant="flat"
                color="warning"
              >
                {id}
              </Chip>
            ))}
          </div>
        </section>
      )}

      {grouped.map((g) => (
        <section key={g.gid} className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-default-500">
              {g.name}
            </h2>
            <div className="flex gap-1">
              <Button
                size="sm"
                variant="light"
                color="danger"
                onPress={() => groupAction(g.services, true)}
              >
                Block all
              </Button>
              <Button
                size="sm"
                variant="light"
                onPress={() => groupAction(g.services, false)}
              >
                Unblock all
              </Button>
            </div>
          </div>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {g.services.map((s) => (
              <ServiceCard
                key={s.id}
                service={s}
                blocked={selected.has(s.id)}
                onToggle={(v) => toggle(s.id, v)}
                disabled={setApps.isPending}
              />
            ))}
          </div>
        </section>
      ))}

      {grouped.length === 0 && (
        <EmptyState title="No services match your filter" />
      )}
    </div>
  );
}

function ServiceCard({
  service,
  blocked,
  onToggle,
  disabled,
}: {
  service: Service;
  blocked: boolean;
  onToggle: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label
      className={`flex items-center justify-between gap-3 rounded-md border px-3 py-2 transition-colors ${
        blocked
          ? "border-danger-200 bg-danger-50/40 dark:bg-danger-900/10"
          : "border-default-200"
      }`}
    >
      <div className="flex items-center gap-2 min-w-0">
        {service.icon_svg ? (
          <img
            src={`data:image/svg+xml;base64,${service.icon_svg}`}
            alt=""
            className="h-5 w-5 shrink-0"
          />
        ) : (
          <span className="h-5 w-5 shrink-0 rounded bg-default-200" />
        )}
        <span className="text-sm truncate">{service.name}</span>
      </div>
      <Switch
        size="sm"
        color="danger"
        isSelected={blocked}
        onValueChange={onToggle}
        isDisabled={disabled}
        aria-label={`Block ${service.name}`}
      />
    </label>
  );
}

function KidActivityTab({ name }: { name: string }) {
  const params = useMemo(() => ({ kid: name, limit: 50 }), [name]);
  const events = useActivity(params);
  return (
    <div className="flex flex-col gap-2">
      {events.isLoading && <Spinner />}
      {events.data && events.data.length === 0 && (
        <EmptyState title="No recent activity" />
      )}
      {events.data && events.data.length > 0 && (
        <ActivityTable events={events.data} hideKid />
      )}
    </div>
  );
}
