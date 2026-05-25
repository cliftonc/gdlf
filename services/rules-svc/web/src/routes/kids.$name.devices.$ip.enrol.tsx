import { useState } from "react";
import {
  createFileRoute,
  Link,
} from "@tanstack/react-router";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  Chip,
  Snippet,
  Spinner,
  Switch,
  Tooltip,
} from "@heroui/react";
import { useConfirm } from "../lib/hooks/useConfirm";
import { useEnrolment, useHandshake, useShortlink } from "../lib/queries";
import {
  useAndroidMdmEnrollToken,
  useCreateShortlink,
  useDeleteShortlink,
  useMarkMitmInstalled,
  useMdmEnrollToken,
  useWindowsMdmEnrollPackage,
  useWindowsMdmMarkEnrolled,
  type WindowsPackageResponse,
} from "../lib/mutations";

export const Route = createFileRoute("/kids/$name/devices/$ip/enrol")({
  component: DeviceEnrolPage,
});

function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${Math.round(b / 1024)} KiB`;
  return `${(b / 1024 / 1024).toFixed(1)} MiB`;
}

/** Surfaced above the manual WG/CA steps when the device is iOS. MDM is the
 *  preferred path — it pushes the WG always-on config + CA trust + bypass
 *  restrictions as a single non-removable profile, so the kid can't undo
 *  any of it. Once enrolled, the manual Steps 1+3 below become redundant. */
function MdmEnrolCard({
  kidName,
  ip,
  mdmStatus,
  dlCode,
}: {
  kidName: string;
  ip: string;
  mdmStatus: "pending" | "enrolled" | "checked_out" | null;
  dlCode?: string | null;
}) {
  const [enrollUrl, setEnrollUrl] = useState<string | null>(null);
  const enrollToken = useMdmEnrollToken(kidName, dlCode);

  const onGenerate = async () => {
    const r = await enrollToken.mutateAsync(ip);
    setEnrollUrl(r.enroll_url);
  };

  const alreadyEnrolled = mdmStatus === "enrolled";

  return (
    <Card className="border border-primary-200 bg-primary-50/30">
      <CardHeader className="flex flex-col items-start gap-1">
        <div className="flex items-center gap-2">
          <p className="text-sm font-semibold">Recommended · iOS supervised MDM</p>
          {alreadyEnrolled ? (
            <Chip size="sm" color="success" variant="flat">
              enrolled
            </Chip>
          ) : (
            <Chip size="sm" color="primary" variant="flat">
              one-shot setup
            </Chip>
          )}
        </div>
        <p className="text-xs text-default-500">
          The MDM profile pushes the WireGuard always-on tunnel, the inspection CA,
          and bypass-blocking restrictions — all locked so the kid can't undo them.
          Replaces Steps 1 + 3 below.
        </p>
      </CardHeader>
      <CardBody className="flex flex-col gap-3">
        {alreadyEnrolled ? (
          <p className="text-sm text-success">
            This device is already MDM-enrolled. Use the MDM dialog on the kid page to
            re-push policy or query state.
          </p>
        ) : (
          <>
            <ol className="list-decimal pl-5 text-sm space-y-1 text-default-700">
              <li>Cable the iPhone to a Mac running Apple Configurator 2.</li>
              <li>
                In Configurator: <em>Prepare…</em> → <em>Manual Configuration</em>,
                tick <em>Supervise devices</em>, then <em>Add to Device Enrollment Program</em>.
              </li>
              <li>When prompted for an MDM server URL, paste the link below.</li>
              <li>
                Continue. The device wipes + supervises + enrolls. WG, CA, and
                restrictions install automatically within ~30s of enrolment.
              </li>
            </ol>
            {enrollUrl ? (
              <div className="flex flex-col gap-2">
                <Snippet
                  size="sm"
                  symbol=""
                  classNames={{ pre: "whitespace-pre-wrap break-all" }}
                >
                  {enrollUrl}
                </Snippet>
                <p className="text-xs text-warning">
                  Valid for 30 minutes. Single-use — generate a new one if Configurator
                  fails partway.
                </p>
              </div>
            ) : (
              <div>
                <Button
                  size="sm"
                  color="primary"
                  onPress={onGenerate}
                  isLoading={enrollToken.isPending}
                >
                  Generate enrolment URL
                </Button>
              </div>
            )}
          </>
        )}
      </CardBody>
    </Card>
  );
}

/** Android equivalent of MdmEnrolCard. The factory-reset → tap-6× → scan-QR
 *  flow is the only way to provision a device as AMAPI Device Owner, but
 *  once done, every Step 1/3 below is redundant — the policy installs WG
 *  + the CA + lockdown restrictions automatically. */
function AndroidMdmEnrolCard({
  kidName,
  ip,
  status,
  dlCode,
}: {
  kidName: string;
  ip: string;
  status: "pending" | "active" | "disabled" | "deleted" | null;
  dlCode?: string | null;
}) {
  const [qrUrl, setQrUrl] = useState<string | null>(null);
  const enroll = useAndroidMdmEnrollToken(kidName, dlCode);

  const onGenerate = async () => {
    const r = await enroll.mutateAsync(ip);
    setQrUrl(`${r.qr_url}?t=${Date.now()}`);
  };

  const alreadyActive = status === "active";

  return (
    <Card className="border border-primary-200 bg-primary-50/30">
      <CardHeader className="flex flex-col items-start gap-1">
        <div className="flex items-center gap-2">
          <p className="text-sm font-semibold">Recommended · Android Management API</p>
          {alreadyActive ? (
            <Chip size="sm" color="success" variant="flat">
              active
            </Chip>
          ) : status === "pending" ? (
            <Chip size="sm" color="warning" variant="flat">
              pending
            </Chip>
          ) : (
            <Chip size="sm" color="primary" variant="flat">
              one-shot setup
            </Chip>
          )}
        </div>
        <p className="text-xs text-default-500">
          Provisions as Device Owner: force-installs WireGuard with the
          tunnel pre-configured, pins it as always-on with lockdown, installs
          the inspection CA as system-trusted, and blocks bypass paths.
          Replaces Steps 1 + 3 below.
        </p>
      </CardHeader>
      <CardBody className="flex flex-col gap-3">
        {alreadyActive ? (
          <p className="text-sm text-success">
            This device is already AMAPI-enrolled. Use the MDM dialog on the kid page to
            re-push policy, refresh status, or unenroll.
          </p>
        ) : (
          <>
            <ol className="list-decimal pl-5 text-sm space-y-1 text-default-700">
              <li>
                Factory-reset the phone (Settings → System → Reset, or skip if it's fresh).
              </li>
              <li>
                On the welcome screen, <strong>tap six times in the same spot</strong> —
                the QR scanner opens.
              </li>
              <li>Scan the QR. The phone provisions automatically (~2 min).</li>
            </ol>
            {qrUrl ? (
              <div className="flex flex-col sm:flex-row items-start gap-4">
                <div className="bg-white p-3 rounded-medium shrink-0">
                  <img
                    src={qrUrl}
                    alt="Android enrollment QR"
                    className="w-64 h-64"
                  />
                </div>
                <div className="flex flex-col gap-2">
                  <Button
                    size="sm"
                    variant="flat"
                    onPress={onGenerate}
                    isLoading={enroll.isPending}
                  >
                    Regenerate
                  </Button>
                  <p className="text-xs text-warning">
                    Single-use, valid for 1 hour. Generate a new one if setup fails partway.
                  </p>
                </div>
              </div>
            ) : (
              <div>
                <Button
                  size="sm"
                  color="primary"
                  onPress={onGenerate}
                  isLoading={enroll.isPending}
                >
                  Generate enrolment QR
                </Button>
              </div>
            )}
          </>
        )}
      </CardBody>
    </Card>
  );
}

/** Windows equivalent of MdmEnrolCard / AndroidMdmEnrolCard.
 *  Builds a downloadable .zip the parent applies as Administrator on the
 *  kid's PC. No live channel — confirmation is via Mark applied below.
 *  Replaces Steps 1 + 3 below when applied.
 */
function WindowsMdmEnrolCard({
  kidName,
  ip,
  status,
  dlCode,
}: {
  kidName: string;
  ip: string;
  status: "pending" | "enrolled" | "revoked" | null;
  dlCode?: string | null;
}) {
  const [pkg, setPkg] = useState<WindowsPackageResponse | null>(null);
  const build = useWindowsMdmEnrollPackage(kidName, dlCode);
  const markApplied = useWindowsMdmMarkEnrolled(kidName, dlCode);

  const onBuild = async () => {
    const r = await build.mutateAsync(ip);
    setPkg(r);
  };

  const onMarkApplied = () => markApplied.mutate(ip);

  const enrolled = status === "enrolled";
  // Show Mark applied as soon as a package has been built (status flips to
  // "pending" server-side), OR right after the parent built one this session
  // (`pkg`) — covers the case where useEnrolment hasn't refetched yet.
  const canMarkApplied = !enrolled && (status === "pending" || pkg !== null);

  return (
    <Card className="border border-primary-200 bg-primary-50/30">
      <CardHeader className="flex flex-col items-start gap-1">
        <div className="flex items-center gap-2">
          <p className="text-sm font-semibold">Recommended · Windows enrolment bundle</p>
          {enrolled ? (
            <Chip size="sm" color="success" variant="flat">
              enrolled
            </Chip>
          ) : status === "pending" ? (
            <Chip size="sm" color="warning" variant="flat">
              pending apply
            </Chip>
          ) : (
            <Chip size="sm" color="primary" variant="flat">
              one-shot setup
            </Chip>
          )}
        </div>
        <p className="text-xs text-default-500">
          A .zip the parent extracts and runs once as Administrator. Installs
          the gdlf CA, WireGuard, and a SYSTEM scheduled task that re-asserts
          state every 5 minutes. The kid must run as a Standard User for the
          containment (WG service ACL + locked tray UI) to bite.
          Replaces Steps 1 + 3 below.
        </p>
      </CardHeader>
      <CardBody className="flex flex-col gap-3">
        {enrolled ? (
          <p className="text-sm text-success">
            This device is enrolled. Use the MDM dialog on the kid page to
            re-build the bundle or issue an un-enrol .zip.
          </p>
        ) : (
          <>
            <ol className="list-decimal pl-5 text-sm space-y-1 text-default-700">
              <li>
                On the kid's PC: confirm the kid's account is a <strong>Standard
                user</strong>, and the parent's account is the local Administrator.
              </li>
              <li>Build the .zip below; download it.</li>
              <li>
                Copy it to the kid's PC (USB / OneDrive / share), sign in there
                as the parent, extract the zip, and <strong>right-click
                Install.cmd → Run as administrator</strong> (or double-click
                Install.cmd and click Yes at the UAC prompt). The script runs
                in ~30s.
              </li>
              <li>
                Once the script prints "gdlf enrolment complete", it
                phones home and this page should flip to <em>enrolled</em>
                within a few seconds. If it doesn't, click <em>Mark
                applied</em> below.
              </li>
            </ol>
            {pkg ? (
              <div className="flex flex-col gap-2">
                <Snippet
                  size="sm"
                  symbol=""
                  classNames={{ pre: "whitespace-pre-wrap break-all" }}
                >
                  {pkg.download_url}
                </Snippet>
                <div className="flex gap-2 flex-wrap items-center">
                  <Button as="a" href={pkg.download_url} size="sm" color="primary">
                    Download .zip
                  </Button>
                  <Button
                    size="sm"
                    variant="flat"
                    onPress={onBuild}
                    isLoading={build.isPending}
                  >
                    Rebuild
                  </Button>
                </div>
                <p className="text-xs text-warning">
                  Single-use download, valid 24h. Re-build if the link expires.
                </p>
              </div>
            ) : (
              <div>
                <Button
                  size="sm"
                  color="primary"
                  onPress={onBuild}
                  isLoading={build.isPending}
                >
                  Build .zip
                </Button>
              </div>
            )}
            {canMarkApplied && (
              <div className="flex items-center gap-3 pt-2 border-t border-primary-100">
                <Button
                  size="sm"
                  color="success"
                  variant="flat"
                  onPress={onMarkApplied}
                  isLoading={markApplied.isPending}
                >
                  Mark applied
                </Button>
                <p className="text-xs text-default-500">
                  Click once you've successfully applied the .ppkg on the kid's PC.
                </p>
              </div>
            )}
          </>
        )}
      </CardBody>
    </Card>
  );
}

/** Parent-only inline shortlink controls, rendered next to the page
 *  header. The URL itself is the affordance — `select-all` so a single
 *  click selects it for copy; icon-only buttons handle copy / rotate /
 *  revoke without burning vertical space. */
function ShareLinkInline({ ip }: { ip: string }) {
  const link = useShortlink(ip);
  const create = useCreateShortlink(ip);
  const del = useDeleteShortlink(ip);
  const confirm = useConfirm();
  const [copied, setCopied] = useState(false);

  const shortUrl =
    link.data?.url
      ? `${window.location.origin}${link.data.url}`
      : null;

  const onCopy = async () => {
    if (!shortUrl) return;
    try {
      await navigator.clipboard.writeText(shortUrl);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard may be unavailable on non-HTTPS dev origins; ignore */
    }
  };

  const onRotate = async () => {
    if (link.data) {
      const ok = await confirm({
        title: "Rotate enrolment shortlink?",
        body: "The current link stops working immediately.",
        confirmLabel: "Rotate",
        danger: true,
      });
      if (!ok) return;
    }
    await create.mutateAsync();
  };

  const onRevoke = async () => {
    const ok = await confirm({
      title: "Revoke enrolment shortlink?",
      body: "The current link stops working immediately. No replacement is generated.",
      confirmLabel: "Revoke",
      danger: true,
    });
    if (!ok) return;
    await del.mutateAsync();
  };

  if (link.isLoading) return <Spinner size="sm" />;

  if (!shortUrl) {
    return (
      <Tooltip content="Generate shortlink">
        <Button
          isIconOnly
          size="sm"
          variant="flat"
          aria-label="Generate enrolment shortlink"
          onPress={onRotate}
          isLoading={create.isPending}
        >
          <ShareIcon className="w-4 h-4" />
        </Button>
      </Tooltip>
    );
  }

  return (
    <div className="flex items-center gap-1.5 rounded-medium border border-primary-200 bg-primary-50/40 dark:bg-primary-50/10 px-2 py-1">
      <ShareIcon className="w-4 h-4 text-primary shrink-0" />
      <span className="font-mono text-sm select-all break-all">{shortUrl}</span>
      <Tooltip content={copied ? "Copied" : "Copy"}>
        <Button
          isIconOnly
          size="sm"
          variant="light"
          aria-label="Copy enrolment shortlink"
          onPress={onCopy}
        >
          {copied ? <CheckIcon className="w-4 h-4" /> : <CopyIcon className="w-4 h-4" />}
        </Button>
      </Tooltip>
      <Tooltip content="Rotate">
        <Button
          isIconOnly
          size="sm"
          variant="light"
          aria-label="Rotate enrolment shortlink"
          onPress={onRotate}
          isLoading={create.isPending}
        >
          <RotateIcon className="w-4 h-4" />
        </Button>
      </Tooltip>
      <Tooltip content="Revoke">
        <Button
          isIconOnly
          size="sm"
          variant="light"
          color="danger"
          aria-label="Revoke enrolment shortlink"
          onPress={onRevoke}
          isLoading={del.isPending}
        >
          <TrashIcon className="w-4 h-4" />
        </Button>
      </Tooltip>
    </div>
  );
}

function ShareIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="18" cy="5" r="3" />
      <circle cx="6" cy="12" r="3" />
      <circle cx="18" cy="19" r="3" />
      <line x1="8.59" y1="13.51" x2="15.42" y2="17.49" />
      <line x1="15.41" y1="6.51" x2="8.59" y2="10.49" />
    </svg>
  );
}

function CopyIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

function CheckIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function RotateIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="23 4 23 10 17 10" />
      <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
    </svg>
  );
}

function TrashIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
      <path d="M9 6V4a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2" />
    </svg>
  );
}

/** Shared by the cookie-authenticated /kids/$name/devices/$ip/enrol route
 *  and the public /dl/$code route. Pass `dlCode` on the public path so all
 *  device-scoped API calls authenticate via `?dl=<code>` rather than the
 *  parent's session cookie. */
export function EnrolView({
  name,
  ip,
  dlCode,
}: {
  name: string;
  ip: string;
  dlCode?: string | null;
}) {
  const enrol = useEnrolment(name, ip, dlCode);
  const handshake = useHandshake(ip, true, dlCode);
  const markCa = useMarkMitmInstalled(name, ip, dlCode);

  if (enrol.isLoading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner />
      </div>
    );
  }
  if (enrol.error || !enrol.data) {
    return <p className="text-danger">Failed to load enrolment: {String(enrol.error)}</p>;
  }
  const e = enrol.data;
  const hs = handshake.data;
  const connected = !!hs && hs.last_handshake > 0;

  return (
    <div className="max-w-3xl mx-auto flex flex-col gap-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          {dlCode ? (
            <p className="text-sm text-default-500">Shared enrolment link</p>
          ) : (
            <Link
              to="/kids/$name"
              params={{ name } as never}
              className="text-sm text-default-500 hover:underline"
            >
              ← {name}
            </Link>
          )}
          <h1 className="text-2xl font-semibold mt-1">Enrol {e.device.name}</h1>
          <p className="text-sm text-default-500 font-mono">{e.device.wg_ip}</p>
        </div>
        {!dlCode && (
          <div className="shrink-0 pt-1">
            <ShareLinkInline ip={ip} />
          </div>
        )}
      </div>

      {e.device.platform === "ios" && (
        <MdmEnrolCard
          kidName={name}
          ip={ip}
          mdmStatus={e.device.mdm?.status ?? null}
          dlCode={dlCode}
        />
      )}

      {e.device.platform === "android" && (
        <AndroidMdmEnrolCard
          kidName={name}
          ip={ip}
          status={e.device.android_mdm?.status ?? null}
          dlCode={dlCode}
        />
      )}

      {e.device.platform === "windows" && (
        <WindowsMdmEnrolCard
          kidName={name}
          ip={ip}
          status={e.device.windows_mdm?.status ?? null}
          dlCode={dlCode}
        />
      )}

      <Card>
        <CardHeader className="flex flex-col items-start gap-1">
          <p className="text-sm font-semibold">Step 1 · Install the WireGuard config</p>
          <p className="text-xs text-default-500">
            Install the WireGuard app on the device, then scan this QR or download the config.
          </p>
        </CardHeader>
        <CardBody className="flex flex-col sm:flex-row items-center gap-6">
          <div className="bg-white p-3 rounded-medium shrink-0">
            <img src={e.qr_url} alt="WireGuard QR" className="w-56 h-56 sm:w-64 sm:h-64" />
          </div>
          <div className="flex-1 flex flex-col gap-3">
            <Button as="a" href={e.conf_url} color="primary" variant="flat">
              Download .conf
            </Button>
            <p className="text-xs text-default-500">
              On phones: open the WireGuard app → ＋ → Scan from QR code.
            </p>
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader className="flex flex-col items-start gap-1">
          <p className="text-sm font-semibold">Step 2 · Connection</p>
          <p className="text-xs text-default-500">
            Auto-refreshes every 2s while you complete enrolment.
          </p>
        </CardHeader>
        <CardBody>
          {connected ? (
            <div className="flex items-center gap-3 flex-wrap">
              <Chip color="success" variant="flat">
                Connected
              </Chip>
              <span className="text-sm text-default-500">
                Last handshake {Math.max(0, Math.floor(Date.now() / 1000 - hs!.last_handshake))}s ago ·{" "}
                {formatBytes(hs!.rx)} rx / {formatBytes(hs!.tx)} tx
              </span>
            </div>
          ) : (
            <div className="flex items-center gap-3">
              <Spinner size="sm" />
              <span className="text-sm text-default-500">Waiting for first handshake…</span>
            </div>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader className="flex flex-col items-start gap-1">
          <p className="text-sm font-semibold">Step 3 · Install the inspection certificate</p>
          <p className="text-xs text-default-500">
            Required for URL-path rules. Even with the CA trusted, gdlf splices
            (forwards TLS untouched) for every host except the ones you add to
            the kid's Inspect list — pinned apps Just Work.
          </p>
        </CardHeader>
        <CardBody className="flex flex-col gap-3">
          {e.ca_present ? (
            <div className="flex flex-col sm:flex-row sm:items-center gap-4">
              <div className="bg-white p-3 rounded-medium shrink-0">
                <img src={e.ca_qr_url} alt="CA QR" className="w-40 h-40" />
              </div>
              <div className="flex flex-col gap-3 flex-1">
                <Button as="a" href={e.ca_url} color="primary" variant="flat">
                  Download CA
                </Button>
                <p className="text-xs text-default-500">
                  iOS: install profile → trust in Settings → General → About → Certificate Trust.
                  Android: install as user CA (limited) or device admin if rooted.
                </p>
                <Switch
                  isSelected={e.device.mitm_ca_installed}
                  onValueChange={(v) => markCa.mutate(v)}
                  size="sm"
                >
                  CA installed on this device
                </Switch>
              </div>
            </div>
          ) : (
            <p className="text-sm text-warning">
              CA not generated yet — run <code>./gdlf init</code> on the host first.
            </p>
          )}
        </CardBody>
      </Card>

      {!dlCode && (
        <div className="flex justify-end">
          <Button
            as={Link}
            to="/kids/$name"
            params={{ name } as never}
            color="primary"
          >
            Done
          </Button>
        </div>
      )}
    </div>
  );
}

function DeviceEnrolPage() {
  const { name, ip } = Route.useParams();
  return <EnrolView name={name} ip={ip} />;
}
