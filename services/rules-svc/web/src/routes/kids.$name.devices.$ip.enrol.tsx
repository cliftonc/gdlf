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
} from "@heroui/react";
import { useEnrolment, useHandshake } from "../lib/queries";
import {
  useAndroidMdmEnrollToken,
  useMarkMitmInstalled,
  useMdmEnrollToken,
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
}: {
  kidName: string;
  ip: string;
  mdmStatus: "pending" | "enrolled" | "checked_out" | null;
}) {
  const [enrollUrl, setEnrollUrl] = useState<string | null>(null);
  const enrollToken = useMdmEnrollToken(kidName);

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
}: {
  kidName: string;
  ip: string;
  status: "pending" | "active" | "disabled" | "deleted" | null;
}) {
  const [qrUrl, setQrUrl] = useState<string | null>(null);
  const enroll = useAndroidMdmEnrollToken(kidName);

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

function DeviceEnrolPage() {
  const { name, ip } = Route.useParams();
  const enrol = useEnrolment(name, ip);
  const handshake = useHandshake(ip);
  const markCa = useMarkMitmInstalled(name, ip);

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
      <div>
        <Link
          to="/kids/$name"
          params={{ name }}
          className="text-sm text-default-500 hover:underline"
        >
          ← {name}
        </Link>
        <h1 className="text-2xl font-semibold mt-1">Enrol {e.device.name}</h1>
        <p className="text-sm text-default-500 font-mono">{e.device.wg_ip}</p>
      </div>

      {e.device.platform === "ios" && (
        <MdmEnrolCard kidName={name} ip={ip} mdmStatus={e.device.mdm?.status ?? null} />
      )}

      {e.device.platform === "android" && (
        <AndroidMdmEnrolCard
          kidName={name}
          ip={ip}
          status={e.device.android_mdm?.status ?? null}
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
            Lets gdlf see HTTPS hostnames + paths for URL rules. Without it, only DNS-level filtering works.
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

      <div className="flex justify-end">
        <Button
          as={Link}
          to="/kids/$name"
          params={{ name }}
          color="primary"
        >
          Done
        </Button>
      </div>
    </div>
  );
}
