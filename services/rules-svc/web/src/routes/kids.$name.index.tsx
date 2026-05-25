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
  useBulkCdns,
  useServices,
  useTlsFailures,
} from "../lib/queries";
import {
  useDeleteKid,
  useKidBlock,
  useAddInspect,
  useRemoveInspect,
  useSetBlockedApps,
  useDismissTlsFailure,
} from "../lib/mutations";
import type { Service } from "../lib/schemas";
import { useConfirm } from "../lib/hooks/useConfirm";
import { DeviceRow } from "../components/DeviceRow";
import { KidStatsPanel } from "../components/KidStatsPanel";
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
  "mitm",
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
          key="mitm"
          title={`MITM (${k.mitm_inspect_hosts.length})`}
        >
          <MitmTab name={name} hosts={k.mitm_inspect_hosts} />
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
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,18rem)_minmax(0,1fr)] gap-6 items-start">
      <aside className="order-2 lg:order-1">
        <KidStatsPanel name={name} />
      </aside>
      <div className="order-1 lg:order-2 flex flex-col gap-3 min-w-0">
        {devices.length === 0 ? (
          <EmptyState
            title="No devices yet"
            body="Enrol a phone, tablet, or laptop to start filtering its traffic."
          />
        ) : (
          devices.map((d) => (
            <DeviceRow key={d.wg_ip} kidName={name} device={d} />
          ))
        )}
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
      </div>
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

function MitmTab({
  name,
  hosts,
}: {
  name: string;
  hosts: string[];
}) {
  const addHost = useAddInspect(name);
  const removeHost = useRemoveInspect(name);
  const failures = useTlsFailures(name);
  const dismiss = useDismissTlsFailure();
  const cdns = useBulkCdns();
  const [custom, setCustom] = useState("");

  const submit = () => {
    const v = custom.trim().toLowerCase();
    if (!v) return;
    addHost.mutate(v, { onSuccess: () => setCustom("") });
  };

  const pinned = failures.data ?? [];

  return (
    <div className="flex flex-col gap-6">
      <div className="text-sm text-default-500 leading-relaxed max-w-3xl">
        Splice-by-default: every HTTPS connection is tunneled untouched
        unless its SNI matches a host below. Hosts on this list are{" "}
        <strong>decrypted</strong> by mitmproxy so URL rules can fire on
        paths (e.g. <span className="font-mono">youtube.com/shorts/*</span>).
        Keep the list small — each entry is a potential pinning failure
        (banking apps, social-media SDKs, etc.).
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="flex flex-col gap-6 min-w-0">
          <section className="flex flex-col gap-2">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-default-500">
              MITM hosts
            </h2>
            <div className="flex gap-2">
              <Input
                size="sm"
                placeholder="e.g. *.example.com"
                value={custom}
                onValueChange={setCustom}
                onKeyDown={(e) => {
                  if (e.key === "Enter") submit();
                }}
                className="flex-1"
              />
              <Button
                color="primary"
                onPress={submit}
                isLoading={addHost.isPending}
                isDisabled={!custom.trim()}
              >
                Add
              </Button>
            </div>
            {hosts.length === 0 ? (
              <p className="text-xs text-default-400 italic">
                No MITM hosts. Everything is spliced — add a glob above to
                decrypt that domain for URL-path rules.
              </p>
            ) : (
              <ul className="flex flex-col gap-1">
                {hosts.map((h) => (
                  <li
                    key={h}
                    className="flex items-center justify-between gap-2 rounded-md border border-default-200 px-3 py-2"
                  >
                    <span className="font-mono text-sm">{h}</span>
                    <Button
                      size="sm"
                      variant="light"
                      color="danger"
                      isLoading={removeHost.isPending && removeHost.variables === h}
                      onPress={() => removeHost.mutate(h)}
                    >
                      Remove
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="flex flex-col gap-2">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-default-500">
              Pinned-cert rejections
              {pinned.length > 0 && (
                <span className="ml-2 text-default-400 normal-case font-normal">
                  {pinned.length} domain{pinned.length === 1 ? "" : "s"}
                </span>
              )}
            </h2>
            <p className="text-xs text-default-400">
              MITM hosts whose apps refused our cert. They can't be
              decrypted — remove them here or accept that they'll break.
            </p>
            {failures.isLoading && <Spinner size="sm" />}
            {!failures.isLoading && pinned.length === 0 && (
              <p className="text-xs text-default-400 italic">
                No pinned-cert rejections. Steady state should stay empty.
              </p>
            )}
            {pinned.length > 0 && (
              <Accordion variant="bordered" selectionMode="multiple" isCompact>
                {pinned.map((g) => (
                  <AccordionItem
                    key={g.registrable}
                    aria-label={g.registrable}
                    title={
                      <div className="flex items-center gap-2 min-w-0 pr-2">
                        <span className="font-mono text-sm truncate">
                          {g.registrable}
                        </span>
                        <Chip size="sm" variant="flat" color="warning">
                          {g.children.length}
                          {g.children.length === 1 ? " host" : " hosts"}
                        </Chip>
                        <span className="text-xs text-default-400 whitespace-nowrap">
                          {formatRelative(g.ts_last)}
                        </span>
                      </div>
                    }
                  >
                    <ul className="flex flex-col gap-1 pl-1">
                      {g.children.map((c) => (
                        <li
                          key={`${c.id}-${c.host}`}
                          className="flex items-start justify-between gap-2 py-1"
                        >
                          <div className="flex flex-col gap-0.5 min-w-0">
                            <span className="font-mono text-xs truncate">{c.host}</span>
                            <span className="text-[11px] text-default-400">
                              {c.count}× · last {formatRelative(c.ts_last)}
                              {c.device && ` · ${c.device}`}
                            </span>
                            {c.error && (
                              <span className="text-[11px] text-warning-600 font-mono break-all">
                                {c.error}
                              </span>
                            )}
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
                ))}
              </Accordion>
            )}
          </section>
        </div>

        <aside className="flex flex-col gap-2 min-w-0">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-default-500">
            Bulk-CDN splice list
            {cdns.data && (
              <span className="ml-2 text-default-400 normal-case font-normal">
                {cdns.data.total} patterns
              </span>
            )}
          </h2>
          <p className="text-xs text-default-400">
            Spliced unconditionally regardless of any MITM entry. Game
            downloads, OS updates, video-segment CDNs — bulk binary
            traffic we'd never want to decrypt. Edit in{" "}
            <span className="font-mono">services/rules-svc/src/gdlf/bulk_cdns.py</span>.
          </p>
          {cdns.data && cdns.data.groups.length > 0 && (
            <Accordion variant="bordered" selectionMode="multiple" isCompact>
              {cdns.data.groups.map((g) => (
                <AccordionItem
                  key={g.vendor}
                  aria-label={g.vendor}
                  title={
                    <div className="flex items-center gap-2 min-w-0 pr-2">
                      <span className="text-sm truncate">{g.vendor}</span>
                      <Chip size="sm" variant="flat" color="default">
                        {g.patterns.length}
                      </Chip>
                    </div>
                  }
                >
                  <ul className="flex flex-col gap-0.5 pl-1">
                    {g.patterns.map((p) => (
                      <li key={p} className="font-mono text-xs text-default-600">
                        {p}
                      </li>
                    ))}
                  </ul>
                </AccordionItem>
              ))}
            </Accordion>
          )}
        </aside>
      </div>
    </div>
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
