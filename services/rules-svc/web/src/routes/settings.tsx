import { createFileRoute } from "@tanstack/react-router";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  Chip,
  Spinner,
} from "@heroui/react";
import { useSettings } from "../lib/queries";
import { usePruneNow } from "../lib/mutations";
import { useConfirm } from "../lib/hooks/useConfirm";

export const Route = createFileRoute("/settings")({
  component: SettingsPage,
});

function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KiB`;
  if (b < 1024 * 1024 * 1024) return `${(b / 1024 / 1024).toFixed(1)} MiB`;
  return `${(b / 1024 / 1024 / 1024).toFixed(2)} GiB`;
}

function formatDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "—";
}

function SettingsPage() {
  const s = useSettings();
  const prune = usePruneNow();
  const confirm = useConfirm();

  const onPrune = async () => {
    const ok = await confirm({
      title: "Prune events now?",
      body: "Older events are deleted and the database is VACUUM'd. Cannot be undone.",
      confirmLabel: "Prune",
      danger: true,
    });
    if (!ok) return;
    await prune.mutateAsync();
  };

  if (s.isLoading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner />
      </div>
    );
  }
  if (!s.data) return null;
  const stats = s.data.db_stats;

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Settings</h1>

      <Card>
        <CardHeader>
          <p className="text-sm font-semibold">mitmproxy inspection certificate</p>
        </CardHeader>
        <CardBody className="flex flex-col gap-3">
          <div className="flex items-center gap-3 flex-wrap">
            {s.data.ca_present ? (
              <Chip color="success" variant="flat">
                CA present
              </Chip>
            ) : (
              <Chip color="warning" variant="flat">
                CA missing — run ./gdlf init
              </Chip>
            )}
          </div>
          {s.data.ca_present && (
            <div className="flex items-center gap-3 flex-wrap">
              <Button as="a" href={s.data.ca_url} variant="flat" color="primary">
                Download CA
              </Button>
              <a
                href={s.data.ca_qr_url}
                target="_blank"
                rel="noreferrer"
                className="text-sm text-primary hover:underline"
              >
                Open QR
              </a>
            </div>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <p className="text-sm font-semibold">Activity storage</p>
        </CardHeader>
        <CardBody className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
          <Stat label="Events" value={stats.events.toLocaleString()} />
          <Stat label="DB size" value={formatBytes(stats.db_bytes)} />
          <Stat label="Oldest" value={formatDate(stats.oldest)} />
          <Stat label="Newest" value={formatDate(stats.newest)} />
          <Stat label="Retention" value={`${s.data.retention_days} days`} />
          <Stat
            label="Cap"
            value={`${s.data.max_events.toLocaleString()} events`}
          />
          <Stat label="Timezone" value={s.data.tz} />
          <Stat
            label="WireGuard endpoint"
            value={`${s.data.wg_host}:${s.data.wg_port}`}
          />
        </CardBody>
        <CardBody className="pt-0">
          <Button
            color="danger"
            variant="flat"
            onPress={onPrune}
            isLoading={prune.isPending}
            className="self-start"
          >
            Prune & VACUUM now
          </Button>
        </CardBody>
      </Card>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-default-500">{label}</p>
      <p className="font-mono text-sm break-all">{value}</p>
    </div>
  );
}
