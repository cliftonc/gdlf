import { useMemo, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import {
  Accordion,
  AccordionItem,
  Button,
  Card,
  CardBody,
  CardHeader,
  Chip,
  Select,
  SelectItem,
  Spinner,
  Switch,
  Textarea,
} from "@heroui/react";
import { useSettings } from "../lib/queries";
import {
  type BrowserPolicyInput,
  usePruneNow,
  useUpdateBrowserPolicy,
} from "../lib/mutations";
import { useConfirm } from "../lib/hooks/useConfirm";
import type {
  AndroidBrowser,
  BrowserCatalogEntry,
  BrowserPolicy,
  IosBrowser,
} from "../lib/schemas";

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
              href={
                s.data.internal_url
                  ? `${s.data.internal_url}:${s.data.adguard_ui_port}/`
                  : `${window.location.protocol}//${window.location.hostname}:${s.data.adguard_ui_port}/`
              }
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

      <BrowserPolicyCard
        policy={s.data.browser_policy}
        catalog={s.data.available_browsers}
      />

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

const IOS_BROWSER_OPTIONS: IosBrowser[] = ["chrome", "safari", "firefox", "edge", "brave", "none"];
const ANDROID_BROWSER_OPTIONS: AndroidBrowser[] = [
  "chrome",
  "firefox",
  "edge",
  "brave",
  "samsung_internet",
  "none",
];

function splitLines(s: string): string[] {
  return s.split("\n").map((l) => l.trim()).filter(Boolean);
}

function BrowserPolicyCard({
  policy,
  catalog,
}: {
  policy: BrowserPolicy;
  catalog: BrowserCatalogEntry[];
}) {
  const update = useUpdateBrowserPolicy();
  const confirm = useConfirm();

  // Local form state, seeded from the server. Saved on Save button.
  const [iosBrowser, setIosBrowser] = useState<IosBrowser>(policy.ios.allowed_browser);
  const [iosExtra, setIosExtra] = useState(policy.ios.extra_blocked.join("\n"));
  const [iosUnblocked, setIosUnblocked] = useState(policy.ios.unblocked.join("\n"));
  const [andBrowser, setAndBrowser] = useState<AndroidBrowser>(policy.android.allowed_browser);
  const [andExtra, setAndExtra] = useState(policy.android.extra_blocked.join("\n"));
  const [andUnblocked, setAndUnblocked] = useState(policy.android.unblocked.join("\n"));
  const [cfg, setCfg] = useState(policy.chrome_managed_config);

  const iosLabelFor = useMemo(() => {
    const map: Record<string, string> = {};
    catalog.forEach((c) => (map[c.key] = c.label));
    return (k: string) => map[k] ?? k;
  }, [catalog]);

  const iosOptions = IOS_BROWSER_OPTIONS.map((k) => ({
    key: k,
    label: k === "safari" ? "Safari" : k === "none" ? "None (no browser)" : iosLabelFor(k),
  }));
  const andOptions = ANDROID_BROWSER_OPTIONS.map((k) => ({
    key: k,
    label: k === "none" ? "None (no browser)" : iosLabelFor(k),
  }));

  const onSave = async () => {
    const ok = await confirm({
      title: "Apply new browser policy?",
      body:
        "All enrolled iOS and Android devices will be re-pushed within a few minutes. " +
        "Removed browsers will become unlaunchable on those devices.",
      confirmLabel: "Apply",
    });
    if (!ok) return;
    const body: BrowserPolicyInput = {
      ios: {
        allowed_browser: iosBrowser,
        extra_blocked: splitLines(iosExtra),
        unblocked: splitLines(iosUnblocked),
      },
      android: {
        allowed_browser: andBrowser,
        extra_blocked: splitLines(andExtra),
        unblocked: splitLines(andUnblocked),
      },
      chrome_managed_config: cfg,
    };
    await update.mutateAsync(body);
  };

  const eff = policy.effective;
  return (
    <Card>
      <CardHeader className="flex flex-col items-start gap-1">
        <p className="text-sm font-semibold">Browser policy</p>
        <p className="text-xs text-default-500">
          The kid's traffic is filtered at the network layer no matter which browser they use,
          but some browsers (Firefox, Brave) ship their own DoH or cert store that weaken the
          chain. This locks the device to one browser per platform.
        </p>
      </CardHeader>
      <CardBody className="flex flex-col gap-5">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          <div className="flex flex-col gap-3">
            <p className="text-xs uppercase tracking-wide text-default-500">iOS</p>
            <Select
              label="Allowed browser"
              selectedKeys={[iosBrowser]}
              onSelectionChange={(keys) => {
                const k = Array.from(keys)[0];
                if (k) setIosBrowser(k as IosBrowser);
              }}
            >
              {iosOptions.map((o) => (
                <SelectItem key={o.key}>{o.label}</SelectItem>
              ))}
            </Select>
            {eff && (
              <p className="text-xs text-default-500">
                {eff.ios_blocklist.length} bundle IDs blocked
                {eff.ios_chromium_appconfig_target
                  ? ` · managed config → ${eff.ios_chromium_appconfig_target}`
                  : ""}
              </p>
            )}
          </div>
          <div className="flex flex-col gap-3">
            <p className="text-xs uppercase tracking-wide text-default-500">Android</p>
            <Select
              label="Allowed browser"
              selectedKeys={[andBrowser]}
              onSelectionChange={(keys) => {
                const k = Array.from(keys)[0];
                if (k) setAndBrowser(k as AndroidBrowser);
              }}
            >
              {andOptions.map((o) => (
                <SelectItem key={o.key}>{o.label}</SelectItem>
              ))}
            </Select>
            {eff && (
              <p className="text-xs text-default-500">
                {eff.android_blocklist.length} packages blocked
                {eff.android_force_install ? ` · force-install ${eff.android_force_install}` : ""}
              </p>
            )}
          </div>
        </div>

        <div className="flex flex-col gap-3">
          <p className="text-xs uppercase tracking-wide text-default-500">
            Managed config for Chromium-based browsers
          </p>
          <p className="text-xs text-default-500">
            Applied to Chrome / Edge / Brave on iOS, and Chrome on Android. Has no effect on
            Safari, Firefox, or when no browser is allowed.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Switch
              isSelected={cfg.incognito_disabled}
              onValueChange={(v) => setCfg({ ...cfg, incognito_disabled: v })}
            >
              Disable Incognito / Private mode
            </Switch>
            <Switch
              isSelected={cfg.sync_disabled}
              onValueChange={(v) => setCfg({ ...cfg, sync_disabled: v })}
            >
              Disable Sync
            </Switch>
            <Switch
              isSelected={cfg.signin_disabled}
              onValueChange={(v) => setCfg({ ...cfg, signin_disabled: v })}
            >
              Disable browser sign-in
            </Switch>
            <Switch
              isSelected={cfg.search_suggest_enabled}
              onValueChange={(v) => setCfg({ ...cfg, search_suggest_enabled: v })}
            >
              Enable search suggestions
            </Switch>
          </div>
        </div>

        <Accordion>
          <AccordionItem
            key="advanced"
            aria-label="Advanced overrides"
            title="Advanced — extra IDs to block, or to unblock"
            subtitle="One bundle ID / package name per line"
          >
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
              <Textarea
                label="iOS extra blocked (bundle IDs)"
                value={iosExtra}
                onValueChange={setIosExtra}
                placeholder="com.example.browser"
                minRows={3}
              />
              <Textarea
                label="iOS unblocked (override curated list)"
                value={iosUnblocked}
                onValueChange={setIosUnblocked}
                placeholder="org.mozilla.ios.Firefox"
                minRows={3}
              />
              <Textarea
                label="Android extra blocked (packages)"
                value={andExtra}
                onValueChange={setAndExtra}
                placeholder="com.example.browser"
                minRows={3}
              />
              <Textarea
                label="Android unblocked (override curated list)"
                value={andUnblocked}
                onValueChange={setAndUnblocked}
                placeholder="org.mozilla.firefox"
                minRows={3}
              />
            </div>
          </AccordionItem>
        </Accordion>

        <div>
          <Button
            color="primary"
            onPress={onSave}
            isLoading={update.isPending}
            className="self-start"
          >
            Save browser policy
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}
