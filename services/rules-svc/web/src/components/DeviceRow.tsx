import { useState } from "react";
import { Button, Chip, Switch } from "@heroui/react";
import { Link } from "@tanstack/react-router";
import type { Device } from "../lib/schemas";
import { useConfirm } from "../lib/hooks/useConfirm";
import { useDeleteDevice, useDeviceBlock, useRegenerateDevice } from "../lib/mutations";

function formatAgo(ts: number): string {
  if (!ts) return "never";
  const sec = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${Math.round(b / 1024)} KiB`;
  return `${(b / 1024 / 1024).toFixed(1)} MiB`;
}

export function DeviceRow({ kidName, device }: { kidName: string; device: Device }) {
  const confirm = useConfirm();
  const del = useDeleteDevice(kidName);
  const block = useDeviceBlock(kidName);
  const regen = useRegenerateDevice(kidName);
  const [busy, setBusy] = useState(false);

  const onDelete = async () => {
    const ok = await confirm({
      title: `Remove ${device.name}?`,
      body: "Its WireGuard keys are deleted and the tunnel stops working.",
      confirmLabel: "Remove device",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await del.mutateAsync(device.wg_ip);
    } finally {
      setBusy(false);
    }
  };

  const onRegen = async () => {
    const ok = await confirm({
      title: `Rotate ${device.name}'s key?`,
      body: "The current tunnel stops working until the device re-scans the QR.",
      confirmLabel: "Rotate key",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await regen.mutateAsync(device.wg_ip);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col sm:flex-row sm:items-center gap-3 p-4 border border-default-200 rounded-medium bg-content1">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <p className="font-medium truncate">{device.name}</p>
          <Chip size="sm" variant="flat">
            {device.platform}
          </Chip>
          {device.online ? (
            <Chip size="sm" color="success" variant="flat">
              Online
            </Chip>
          ) : (
            <Chip size="sm" variant="flat">
              Offline
            </Chip>
          )}
          {device.manual_block && (
            <Chip size="sm" color="danger" variant="flat">
              Blocked
            </Chip>
          )}
          {!device.mitm_ca_installed && (
            <Chip size="sm" color="warning" variant="flat">
              CA missing
            </Chip>
          )}
        </div>
        <p className="text-xs text-default-500 mt-1 font-mono">
          {device.wg_ip} · last handshake {formatAgo(device.last_handshake)} ·{" "}
          {formatBytes(device.rx)} rx / {formatBytes(device.tx)} tx
        </p>
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <Switch
          size="sm"
          isSelected={device.manual_block}
          onValueChange={(v) => block.mutate({ ip: device.wg_ip, blocked: v })}
        >
          Block
        </Switch>
        <Button
          as={Link}
          to="/kids/$name/devices/$ip/enrol"
          params={{ name: kidName, ip: device.wg_ip }}
          size="sm"
          variant="flat"
        >
          Enrol
        </Button>
        <Button size="sm" variant="flat" onPress={onRegen} isDisabled={busy}>
          Rotate
        </Button>
        <Button size="sm" color="danger" variant="light" onPress={onDelete} isDisabled={busy}>
          Remove
        </Button>
      </div>
    </div>
  );
}
