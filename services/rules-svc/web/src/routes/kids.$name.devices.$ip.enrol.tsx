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
  Spinner,
  Switch,
} from "@heroui/react";
import { useEnrolment, useHandshake } from "../lib/queries";
import { useMarkMitmInstalled } from "../lib/mutations";

export const Route = createFileRoute("/kids/$name/devices/$ip/enrol")({
  component: DeviceEnrolPage,
});

function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${Math.round(b / 1024)} KiB`;
  return `${(b / 1024 / 1024).toFixed(1)} MiB`;
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
