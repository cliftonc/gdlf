import { useMemo, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import {
  Button,
  Chip,
  Select,
  SelectItem,
  Spinner,
  Switch,
} from "@heroui/react";
import { useActivity, useKids } from "../lib/queries";
import { useActivityStream } from "../lib/hooks/useActivityStream";
import { EmptyState } from "../components/EmptyState";
import { ActivityTable } from "../components/ActivityTable";

export const Route = createFileRoute("/activity")({
  component: ActivityPage,
});

const DECISIONS = ["block", "allow", "flag", "sni_only", "dns_block"];

function ActivityPage() {
  const kids = useKids();
  const [kid, setKid] = useState<string | null>(null);
  const [decision, setDecision] = useState<string | null>(null);
  const [sni, setSni] = useState(false);
  const [assets, setAssets] = useState(false);
  const [paused, setPaused] = useState(false);

  const params = useMemo(
    () => ({ kid, decision, sni, assets, limit: 50 }),
    [kid, decision, sni, assets]
  );

  const events = useActivity(params);
  useActivityStream(paused ? { ...params, kid: "__paused__" } : params);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Activity</h1>
        <div className="flex items-center gap-2">
          <Chip color={paused ? "default" : "success"} variant="dot" size="sm">
            {paused ? "Paused" : "Live"}
          </Chip>
          <Button size="sm" variant="flat" onPress={() => setPaused((p) => !p)}>
            {paused ? "Resume" : "Pause"}
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:flex sm:flex-row gap-3 sm:items-end sm:flex-wrap">
        <Select
          label="Kid"
          size="sm"
          className="w-full sm:w-44"
          selectedKeys={kid ? new Set([kid]) : new Set()}
          onSelectionChange={(keys) => {
            const k = Array.from(keys as Set<string>)[0] ?? null;
            setKid(k || null);
          }}
        >
          <SelectItem key="">All kids</SelectItem>
          <>
            {(kids.data ?? []).map((k) => (
              <SelectItem key={k.name}>{k.name}</SelectItem>
            ))}
          </>
        </Select>
        <Select
          label="Decision"
          size="sm"
          className="w-full sm:w-44"
          selectedKeys={decision ? new Set([decision]) : new Set()}
          onSelectionChange={(keys) => {
            const d = Array.from(keys as Set<string>)[0] ?? null;
            setDecision(d || null);
          }}
        >
          <SelectItem key="">Any decision</SelectItem>
          <>
            {DECISIONS.map((d) => (
              <SelectItem key={d}>{d}</SelectItem>
            ))}
          </>
        </Select>
        <div className="col-span-2 flex gap-4 sm:gap-3 sm:ml-2 sm:pb-2">
          <Switch size="sm" isSelected={sni} onValueChange={setSni}>
            Include SNI
          </Switch>
          <Switch size="sm" isSelected={assets} onValueChange={setAssets}>
            Include assets
          </Switch>
        </div>
      </div>

      {events.isLoading && (
        <div className="flex justify-center py-12">
          <Spinner />
        </div>
      )}

      {events.data && events.data.length === 0 && (
        <EmptyState title="No events match this filter" />
      )}

      {events.data && events.data.length > 0 && (
        <ActivityTable events={events.data} />
      )}
    </div>
  );
}
