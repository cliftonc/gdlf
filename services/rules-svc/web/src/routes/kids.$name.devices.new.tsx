import { useState } from "react";
import {
  createFileRoute,
  Link,
  useNavigate,
} from "@tanstack/react-router";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  Input,
  Select,
  SelectItem,
} from "@heroui/react";
import { useCreateDevice } from "../lib/mutations";
import { PlatformEnum, type Platform } from "../lib/schemas";

const PLATFORMS: { key: Platform; label: string }[] = [
  { key: "ios", label: "iOS / iPadOS" },
  { key: "android", label: "Android" },
  { key: "chromeos", label: "ChromeOS" },
  { key: "windows", label: "Windows" },
  { key: "macos", label: "macOS" },
  { key: "linux", label: "Linux" },
  { key: "other", label: "Other" },
];

export const Route = createFileRoute("/kids/$name/devices/new")({
  component: DeviceNewPage,
});

function DeviceNewPage() {
  const { name } = Route.useParams();
  const nav = useNavigate();
  const create = useCreateDevice(name);
  const [deviceName, setDeviceName] = useState("");
  const [platform, setPlatform] = useState<Platform>("ios");
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const res = await create.mutateAsync({ device_name: deviceName.trim(), platform });
      nav({
        to: "/kids/$name/devices/$ip/enrol",
        params: { name, ip: res.wg_ip },
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create device");
    }
  };

  return (
    <div className="max-w-md mx-auto">
      <Link
        to="/kids/$name"
        params={{ name }}
        className="text-sm text-default-500 hover:underline"
      >
        ← {name}
      </Link>
      <h1 className="text-2xl font-semibold mb-4 mt-1">Add device</h1>
      <Card>
        <CardHeader>
          <p className="text-sm text-default-500">
            Generates a WireGuard keypair, allocates an IP, and renders a config you can scan from the device.
          </p>
        </CardHeader>
        <CardBody>
          <form onSubmit={submit} className="flex flex-col gap-4">
            <Input
              label="Device name"
              placeholder="e.g. Sam's iPhone"
              value={deviceName}
              onValueChange={setDeviceName}
              autoFocus
              isRequired
            />
            <Select
              label="Platform"
              selectedKeys={new Set([platform])}
              onSelectionChange={(keys) => {
                const k = Array.from(keys as Set<string>)[0];
                if (k) setPlatform(PlatformEnum.parse(k));
              }}
            >
              {PLATFORMS.map((p) => (
                <SelectItem key={p.key}>{p.label}</SelectItem>
              ))}
            </Select>
            {error && <p className="text-danger text-sm">{error}</p>}
            <div className="flex justify-end gap-2">
              <Button
                as={Link}
                to="/kids/$name"
                params={{ name }}
                variant="light"
              >
                Cancel
              </Button>
              <Button
                type="submit"
                color="primary"
                isLoading={create.isPending}
                isDisabled={!deviceName.trim()}
              >
                Create device
              </Button>
            </div>
          </form>
        </CardBody>
      </Card>
    </div>
  );
}
