import { useMemo, useState } from "react";
import {
  createFileRoute,
  Link,
  useNavigate,
  useSearch,
} from "@tanstack/react-router";
import {
  Button,
  Spinner,
  Switch,
  Tab,
  Tabs,
} from "@heroui/react";
import { z } from "zod";
import { useKid, useActivity } from "../lib/queries";
import { useDeleteKid, useKidBlock } from "../lib/mutations";
import { useConfirm } from "../lib/hooks/useConfirm";
import { DeviceRow } from "../components/DeviceRow";
import { RuleRow } from "../components/RuleRow";
import { ScheduleEditor } from "../components/ScheduleEditor";
import { BonusControls } from "../components/BonusControls";
import { ActivityTable } from "../components/ActivityTable";
import { EmptyState } from "../components/EmptyState";

const TabEnum = z.enum(["devices", "schedule", "rules", "activity"]);

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
