import { useState } from "react";
import {
  Button,
  Chip,
  Code,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
  Snippet,
  Spinner,
} from "@heroui/react";
import type { Device } from "../lib/schemas";
import { useMdmCommands } from "../lib/queries";
import {
  useAndroidMdmEnrollToken,
  useAndroidMdmSyncPolicy,
  useAndroidMdmSyncStatus,
  useAndroidMdmUnenroll,
  useMdmEnqueueCommand,
  useMdmEnrollToken,
  useMdmInstallPolicy,
  useMdmPush,
  useWindowsMdmEnrollPackage,
  useWindowsMdmMarkEnrolled,
  useWindowsMdmRevoke,
  type WindowsPackageResponse,
} from "../lib/mutations";

function formatAgo(iso: string | null): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return "in the future";
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

function statusColor(s: string): "success" | "warning" | "danger" | "default" {
  if (s === "acknowledged" || s === "Acknowledged" || s === "enrolled" || s === "active")
    return "success";
  if (s === "pending" || s === "sent" || s === "NotNow") return "warning";
  if (
    s === "error" ||
    s === "Error" ||
    s === "CommandFormatError" ||
    s === "checked_out" ||
    s === "disabled" ||
    s === "deleted" ||
    s === "revoked"
  )
    return "danger";
  return "default";
}

export function MdmDialog({
  kidName,
  device,
  isOpen,
  onClose,
}: {
  kidName: string;
  device: Device;
  isOpen: boolean;
  onClose: () => void;
}) {
  const isAndroid = device.platform === "android";
  const isWindows = device.platform === "windows";
  const enrolled = device.mdm?.status === "enrolled";
  const commands = useMdmCommands(
    device.wg_ip,
    isOpen && enrolled && !isAndroid && !isWindows,
  );
  const enrollToken = useMdmEnrollToken(kidName);
  const installPolicy = useMdmInstallPolicy(kidName);
  const push = useMdmPush(kidName);
  const enqueue = useMdmEnqueueCommand(kidName);

  // Android Management API mutations.
  const aEnroll = useAndroidMdmEnrollToken(kidName);
  const aSyncPolicy = useAndroidMdmSyncPolicy(kidName);
  const aSyncStatus = useAndroidMdmSyncStatus(kidName);
  const aUnenroll = useAndroidMdmUnenroll(kidName);

  // Windows .zip mutations.
  const wBuild = useWindowsMdmEnrollPackage(kidName);
  const wMark = useWindowsMdmMarkEnrolled(kidName);
  const wRevoke = useWindowsMdmRevoke(kidName);

  const [enrollUrl, setEnrollUrl] = useState<string | null>(null);
  // QR PNG URL cache-busted on regenerate so the <img> reloads.
  const [aQrUrl, setAQrUrl] = useState<string | null>(null);
  // Most recent .zip download response — kept locally so the parent can
  // copy the link / re-download until the modal closes.
  const [wPackage, setWPackage] = useState<WindowsPackageResponse | null>(null);

  const onGenerateUrl = async () => {
    const r = await enrollToken.mutateAsync(device.wg_ip);
    setEnrollUrl(r.enroll_url);
  };

  const onGenerateAndroidQr = async () => {
    const r = await aEnroll.mutateAsync(device.wg_ip);
    setAQrUrl(`${r.qr_url}?t=${Date.now()}`);
  };

  const onBuildWindowsPackage = async (revoke: boolean) => {
    const r = await (revoke ? wRevoke : wBuild).mutateAsync(device.wg_ip);
    setWPackage(r);
  };

  return (
    <Modal
      isOpen={isOpen}
      onOpenChange={(open) => {
        if (!open) {
          setEnrollUrl(null);
          setAQrUrl(null);
          setWPackage(null);
          onClose();
        }
      }}
      placement="center"
      backdrop="blur"
      size="3xl"
      scrollBehavior="inside"
    >
      <ModalContent>
        <>
          <ModalHeader className="flex flex-col gap-1">
            <span>MDM · {device.name}</span>
            <span className="text-xs text-default-500 font-mono font-normal">
              {device.wg_ip} · {device.platform}
            </span>
          </ModalHeader>
          <ModalBody className="gap-4">
            {isAndroid ? (
              <AndroidMdmBody
                device={device}
                qrUrl={aQrUrl}
                onGenerateQr={onGenerateAndroidQr}
                generating={aEnroll.isPending}
                onSyncPolicy={() => aSyncPolicy.mutate(device.wg_ip)}
                syncingPolicy={aSyncPolicy.isPending}
                onSyncStatus={() => aSyncStatus.mutate(device.wg_ip)}
                syncingStatus={aSyncStatus.isPending}
                onUnenroll={() => aUnenroll.mutate(device.wg_ip)}
                unenrolling={aUnenroll.isPending}
              />
            ) : isWindows ? (
              <WindowsMdmBody
                device={device}
                pkg={wPackage}
                onBuild={() => onBuildWindowsPackage(false)}
                building={wBuild.isPending}
                onRevoke={() => onBuildWindowsPackage(true)}
                revoking={wRevoke.isPending}
                onMarkApplied={() => wMark.mutate(device.wg_ip)}
                marking={wMark.isPending}
              />
            ) : (
              <>
            {/* Status panel ------------------------------------------------ */}
            <div className="flex items-center gap-2 flex-wrap">
              {device.mdm ? (
                <>
                  <Chip size="sm" color={statusColor(device.mdm.status)} variant="flat">
                    {device.mdm.status}
                  </Chip>
                  {device.mdm.supervised && (
                    <Chip size="sm" color="primary" variant="flat">
                      supervised
                    </Chip>
                  )}
                  {device.mdm.udid && (
                    <Chip size="sm" variant="flat">
                      UDID {device.mdm.udid.slice(0, 8)}…
                    </Chip>
                  )}
                  <span className="text-xs text-default-500">
                    last check-in: {formatAgo(device.mdm.last_checkin_at)}
                  </span>
                </>
              ) : (
                <Chip size="sm" variant="flat">
                  not enrolled
                </Chip>
              )}
            </div>

            {/* Enrolment flow --------------------------------------------- */}
            {!enrolled && (
              <div className="border border-default-200 rounded-medium p-3 bg-content2/40">
                <p className="text-sm font-medium mb-2">Enrol with Apple Configurator</p>
                <ol className="list-decimal pl-5 text-sm space-y-1 mb-3 text-default-700">
                  <li>Cable the iPhone to a Mac running Apple Configurator 2.</li>
                  <li>
                    In Configurator: <em>Prepare…</em> → <em>Manual Configuration</em>,
                    tick <em>Supervise devices</em>, then <em>Add to Device Enrollment Program</em>.
                  </li>
                  <li>When prompted for an MDM server URL, paste the link below.</li>
                  <li>Continue through the prompts; the device wipes + supervises + enrols.</li>
                </ol>
                {enrollUrl ? (
                  <Snippet
                    size="sm"
                    symbol=""
                    classNames={{ pre: "whitespace-pre-wrap break-all" }}
                  >
                    {enrollUrl}
                  </Snippet>
                ) : (
                  <Button
                    size="sm"
                    color="primary"
                    onPress={onGenerateUrl}
                    isLoading={enrollToken.isPending}
                  >
                    Generate enrolment URL
                  </Button>
                )}
                {enrollUrl && (
                  <p className="text-xs text-warning mt-2">
                    Valid for 30 minutes. Single-use — generate a new one if Configurator
                    fails partway.
                  </p>
                )}
              </div>
            )}

            {/* Enrolled actions ------------------------------------------- */}
            {enrolled && (
              <div className="flex gap-2 flex-wrap">
                <Button
                  size="sm"
                  variant="flat"
                  onPress={() => installPolicy.mutate(device.wg_ip)}
                  isLoading={installPolicy.isPending}
                >
                  Re-install policy
                </Button>
                <Button
                  size="sm"
                  variant="flat"
                  onPress={() =>
                    enqueue.mutate({ ip: device.wg_ip, request_type: "DeviceInformation" })
                  }
                  isLoading={enqueue.isPending}
                >
                  Query device info
                </Button>
                <Button
                  size="sm"
                  variant="flat"
                  onPress={() =>
                    enqueue.mutate({
                      ip: device.wg_ip,
                      request_type: "InstalledApplicationList",
                    })
                  }
                >
                  Query installed apps
                </Button>
                <Button
                  size="sm"
                  variant="flat"
                  onPress={() => push.mutate(device.wg_ip)}
                  isLoading={push.isPending}
                >
                  Wake (blank push)
                </Button>
              </div>
            )}

            {/* Command queue + response history --------------------------- */}
            {enrolled && (
              <div>
                <p className="text-sm font-medium mb-2">Command history</p>
                {commands.isLoading && <Spinner size="sm" />}
                {commands.data && commands.data.queue.length === 0 && (
                  <p className="text-xs text-default-500">No commands sent yet.</p>
                )}
                {commands.data && commands.data.queue.length > 0 && (
                  <div className="border border-default-200 rounded-medium overflow-hidden">
                    <table className="w-full text-xs">
                      <thead className="bg-content2">
                        <tr>
                          <th className="text-left px-2 py-1.5">Request</th>
                          <th className="text-left px-2 py-1.5">Status</th>
                          <th className="text-left px-2 py-1.5">Queued</th>
                          <th className="text-left px-2 py-1.5">Completed</th>
                          <th className="text-left px-2 py-1.5">UUID</th>
                        </tr>
                      </thead>
                      <tbody>
                        {commands.data.queue.map((c) => (
                          <tr key={c.command_uuid} className="border-t border-default-100">
                            <td className="px-2 py-1.5">{c.request_type}</td>
                            <td className="px-2 py-1.5">
                              <Chip size="sm" variant="flat" color={statusColor(c.status)}>
                                {c.status}
                              </Chip>
                            </td>
                            <td className="px-2 py-1.5 text-default-500">
                              {formatAgo(c.created_at)}
                            </td>
                            <td className="px-2 py-1.5 text-default-500">
                              {c.completed_at ? formatAgo(c.completed_at) : "—"}
                            </td>
                            <td className="px-2 py-1.5 font-mono text-default-400">
                              {c.command_uuid.slice(0, 8)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {commands.data && commands.data.responses.length > 0 && (
                  <details className="mt-3">
                    <summary className="cursor-pointer text-xs text-default-500">
                      Raw responses ({commands.data.responses.length})
                    </summary>
                    <div className="mt-2 space-y-2">
                      {commands.data.responses.map((r) => (
                        <div key={r.command_uuid + r.ts} className="text-xs">
                          <div className="flex items-center gap-2">
                            <Chip size="sm" variant="flat" color={statusColor(r.status)}>
                              {r.status}
                            </Chip>
                            <span className="font-mono text-default-400">
                              {r.command_uuid.slice(0, 8)}
                            </span>
                            <span className="text-default-500">{formatAgo(r.ts)}</span>
                          </div>
                          <Code className="mt-1 max-h-32 overflow-auto block whitespace-pre-wrap text-[10px]">
                            {r.response_excerpt}
                          </Code>
                        </div>
                      ))}
                    </div>
                  </details>
                )}
              </div>
            )}
              </>
            )}
          </ModalBody>
          <ModalFooter>
            <Button variant="light" onPress={onClose}>
              Close
            </Button>
          </ModalFooter>
        </>
      </ModalContent>
    </Modal>
  );
}


// ---------------------------------------------------------------------------
// Android Management API body. Much simpler than the iOS one — there's no
// command queue, no command responses, no APNs push. Just: generate a QR,
// scan it on a factory-reset phone, then watch status flip from pending
// to active. Policy updates propagate automatically via the kids.yaml
// mutation watcher; the buttons here are manual override.

function AndroidMdmBody({
  device,
  qrUrl,
  onGenerateQr,
  generating,
  onSyncPolicy,
  syncingPolicy,
  onSyncStatus,
  syncingStatus,
  onUnenroll,
  unenrolling,
}: {
  device: Device;
  qrUrl: string | null;
  onGenerateQr: () => void;
  generating: boolean;
  onSyncPolicy: () => void;
  syncingPolicy: boolean;
  onSyncStatus: () => void;
  syncingStatus: boolean;
  onUnenroll: () => void;
  unenrolling: boolean;
}) {
  const state = device.android_mdm;
  const active = state?.status === "active";

  return (
    <>
      <div className="flex items-center gap-2 flex-wrap">
        {state ? (
          <>
            <Chip size="sm" color={statusColor(state.status)} variant="flat">
              {state.status}
            </Chip>
            {state.model && (
              <Chip size="sm" variant="flat">
                {state.model}
              </Chip>
            )}
            {state.applied_policy_version && (
              <Chip size="sm" variant="flat">
                policy v{state.applied_policy_version}
              </Chip>
            )}
            <span className="text-xs text-default-500">
              last status: {formatAgo(state.last_status_at)}
            </span>
          </>
        ) : (
          <Chip size="sm" variant="flat">
            not enrolled
          </Chip>
        )}
      </div>

      {!active && (
        <div className="border border-default-200 rounded-medium p-3 bg-content2/40">
          <p className="text-sm font-medium mb-2">
            Enrol with Android Management API
          </p>
          <ol className="list-decimal pl-5 text-sm space-y-1 mb-3 text-default-700">
            <li>Factory-reset the phone (Settings → System → Reset, or skip if new).</li>
            <li>
              On the welcome screen, tap the screen <em>six times in the same spot</em> —
              the QR scanner opens.
            </li>
            <li>Scan the QR code below. The phone provisions automatically (~2 min).</li>
          </ol>

          {qrUrl ? (
            <div className="flex flex-col items-start gap-2">
              <img
                src={qrUrl}
                alt="Android enrollment QR"
                className="w-64 h-64 border border-default-200 rounded"
              />
              <div className="flex gap-2">
                <Button size="sm" variant="flat" onPress={onGenerateQr} isLoading={generating}>
                  Regenerate
                </Button>
              </div>
              <p className="text-xs text-warning">
                Single-use, valid for 1 hour. Generate a new one if setup fails partway.
              </p>
            </div>
          ) : (
            <Button
              size="sm"
              color="primary"
              onPress={onGenerateQr}
              isLoading={generating}
            >
              Generate enrolment QR
            </Button>
          )}
        </div>
      )}

      {state && (
        <div className="flex gap-2 flex-wrap">
          <Button
            size="sm"
            variant="flat"
            onPress={onSyncPolicy}
            isLoading={syncingPolicy}
          >
            Re-push policy
          </Button>
          <Button
            size="sm"
            variant="flat"
            onPress={onSyncStatus}
            isLoading={syncingStatus}
          >
            Refresh status
          </Button>
          <Button
            size="sm"
            variant="flat"
            color="danger"
            onPress={onUnenroll}
            isLoading={unenrolling}
          >
            Unenroll
          </Button>
        </div>
      )}

      {state?.device_name && (
        <details className="text-xs text-default-500">
          <summary className="cursor-pointer">AMAPI resource names</summary>
          <div className="mt-1 font-mono break-all">
            <div>device: {state.device_name}</div>
          </div>
        </details>
      )}
    </>
  );
}


// ---------------------------------------------------------------------------
// Windows body. The only platform without a live channel — we hand the parent
// a one-shot .zip they extract and run on the kid's PC as Administrator
// (Install.cmd → UAC). After that, "Mark applied" is parent-attested
// confirmation. Revoking issues a matching uninstall .zip.

function WindowsMdmBody({
  device,
  pkg,
  onBuild,
  building,
  onRevoke,
  revoking,
  onMarkApplied,
  marking,
}: {
  device: Device;
  pkg: WindowsPackageResponse | null;
  onBuild: () => void;
  building: boolean;
  onRevoke: () => void;
  revoking: boolean;
  onMarkApplied: () => void;
  marking: boolean;
}) {
  const state = device.windows_mdm;
  const enrolled = state?.status === "enrolled";
  const pendingApply = !!state && state.status === "pending";
  const pendingRevoke = !!state && state.status === "revoked";

  return (
    <>
      <div className="flex items-center gap-2 flex-wrap">
        {state ? (
          <>
            <Chip size="sm" color={statusColor(state.status)} variant="flat">
              {state.status}
            </Chip>
            <Chip size="sm" variant="flat">
              v{state.package_version}
            </Chip>
            <span className="text-xs text-default-500">
              built {formatAgo(state.last_built_at)}
              {state.enrolled_at && ` · applied ${formatAgo(state.enrolled_at)}`}
            </span>
          </>
        ) : (
          <Chip size="sm" variant="flat">
            not enrolled
          </Chip>
        )}
      </div>

      <div className="text-xs text-default-500 bg-content2/40 border border-default-200 rounded-medium p-3">
        Windows has no live MDM channel — we hand the parent a one-shot
        .zip with a self-elevating <code>Install.cmd</code> inside. The
        parent applies it as <strong>Administrator</strong>; the kid must
        run as a <strong>Standard User</strong> for the containment (WG
        service ACL + locked tray UI) to bite. See <code>docs/setup-windows-mdm.md</code>.
      </div>

      {!enrolled && !pendingRevoke && (
        <div className="border border-default-200 rounded-medium p-3 bg-content2/40">
          <p className="text-sm font-medium mb-2">
            Build the enrolment bundle
          </p>
          <ol className="list-decimal pl-5 text-sm space-y-1 mb-3 text-default-700">
            <li>
              Make sure the kid's PC account is a <strong>Standard user</strong> and
              the parent's account is the local Administrator.
            </li>
            <li>Click <em>Build .zip</em>; download the file.</li>
            <li>
              Copy it to the kid's PC (USB, OneDrive, share). Sign in there
              as the parent / Administrator, extract the zip, and
              <strong> right-click Install.cmd → Run as administrator</strong>
              (or double-click and click Yes at the UAC prompt).
            </li>
            <li>
              Come back here and click <em>Mark applied</em> once the WireGuard
              tray icon is up on the device.
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
                <Button
                  as="a"
                  href={pkg.download_url}
                  size="sm"
                  color="primary"
                >
                  Download .zip
                </Button>
                <Button
                  size="sm"
                  variant="flat"
                  onPress={onBuild}
                  isLoading={building}
                >
                  Rebuild
                </Button>
                <span className="text-xs text-default-500 font-mono">
                  {pkg.package_id} · v{pkg.package_version}
                </span>
              </div>
              <p className="text-xs text-warning">
                Single-use download, valid 24h. Re-build if the link expires.
              </p>
            </div>
          ) : (
            <Button
              size="sm"
              color="primary"
              onPress={onBuild}
              isLoading={building}
            >
              Build .zip
            </Button>
          )}
        </div>
      )}

      {pendingRevoke && (
        <div className="border border-warning-200 rounded-medium p-3 bg-warning-50/40">
          <p className="text-sm font-medium mb-2">
            Un-enrolment bundle built
          </p>
          <p className="text-sm text-default-700 mb-3">
            Apply the uninstall .zip on the kid's PC (same flow as enrol —
            extract, right-click Uninstall.cmd → Run as administrator).
            Then click <em>Mark applied</em> to clear the device's state here.
          </p>
          {pkg && (
            <div className="flex flex-col gap-2">
              <Snippet
                size="sm"
                symbol=""
                classNames={{ pre: "whitespace-pre-wrap break-all" }}
              >
                {pkg.download_url}
              </Snippet>
              <div className="flex gap-2 flex-wrap items-center">
                <Button
                  as="a"
                  href={pkg.download_url}
                  size="sm"
                  color="warning"
                >
                  Download uninstall .zip
                </Button>
                <Button
                  size="sm"
                  variant="flat"
                  onPress={onRevoke}
                  isLoading={revoking}
                >
                  Rebuild
                </Button>
              </div>
            </div>
          )}
        </div>
      )}

      {(pendingApply || pendingRevoke) && (
        <div className="flex gap-2 flex-wrap">
          <Button
            size="sm"
            color="success"
            variant="flat"
            onPress={onMarkApplied}
            isLoading={marking}
          >
            Mark applied
          </Button>
        </div>
      )}

      {enrolled && (
        <div className="flex gap-2 flex-wrap">
          <Button
            size="sm"
            variant="flat"
            onPress={onBuild}
            isLoading={building}
          >
            Re-build .ppkg
          </Button>
          <Button
            size="sm"
            variant="flat"
            color="danger"
            onPress={onRevoke}
            isLoading={revoking}
          >
            Build un-enrol .ppkg
          </Button>
        </div>
      )}

      {state && (
        <details className="text-xs text-default-500">
          <summary className="cursor-pointer">Package metadata</summary>
          <div className="mt-1 font-mono break-all space-y-0.5">
            <div>id:      {state.package_id}</div>
            <div>version: {state.package_version}</div>
          </div>
        </details>
      )}
    </>
  );
}
