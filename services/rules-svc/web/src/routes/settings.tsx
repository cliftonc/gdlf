import { useState } from "react";
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
  const [showAgPw, setShowAgPw] = useState(false);

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
          <p className="text-sm font-semibold">AdGuard admin</p>
        </CardHeader>
        <CardBody className="flex flex-col gap-3">
          <p className="text-sm text-default-500">
            DNS filtering UI. Sign in with the credentials below; rules-svc
            syncs per-kid client config every 60s on top.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
            <Stat label="Username" value={s.data.adguard_admin_user} />
            <div>
              <p className="text-xs uppercase tracking-wide text-default-500">Password</p>
              <div className="flex items-center gap-2">
                <p className="font-mono text-sm break-all">
                  {showAgPw
                    ? s.data.adguard_admin_password || "(unset)"
                    : "••••••••••••"}
                </p>
                <Button
                  size="sm"
                  variant="light"
                  onPress={() => setShowAgPw((v) => !v)}
                >
                  {showAgPw ? "Hide" : "Show"}
                </Button>
                {s.data.adguard_admin_password && (
                  <Button
                    size="sm"
                    variant="light"
                    onPress={() =>
                      navigator.clipboard?.writeText(s.data!.adguard_admin_password)
                    }
                  >
                    Copy
                  </Button>
                )}
              </div>
            </div>
          </div>
          <div>
            <Button
              as="a"
              href={`${window.location.protocol}//${window.location.hostname}:${s.data.adguard_ui_port}/`}
              target="_blank"
              rel="noreferrer"
              color="primary"
              variant="flat"
            >
              Open AdGuard
            </Button>
          </div>
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
