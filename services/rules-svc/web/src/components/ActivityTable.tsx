import { Link } from "@tanstack/react-router";
import {
  Button,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableColumn,
  TableHeader,
  TableRow,
} from "@heroui/react";
import type { Event } from "../lib/schemas";

function decisionColor(decision: string) {
  if (decision === "block" || decision === "dns_block") return "danger";
  if (decision === "flag") return "warning";
  if (decision === "sni_only") return "default";
  return "success";
}

function formatTime(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString([], { hour12: false });
}

export function ActivityTable({
  events,
  hideKid = false,
}: {
  events: Event[];
  hideKid?: boolean;
}) {
  return (
    <Table
      isCompact
      aria-label="Activity events"
      classNames={{
        base: "overflow-x-auto",
        table: "min-w-[640px]",
        th: "text-xs uppercase tracking-wide",
        td: "py-1.5 align-middle",
      }}
    >
      <TableHeader>
        <TableColumn key="time" width={80}>
          Time
        </TableColumn>
        <TableColumn key="decision" width={90}>
          Decision
        </TableColumn>
        <TableColumn key="kind" width={70} className="hidden sm:table-cell">
          Kind
        </TableColumn>
        <TableColumn
          key="who"
          width={hideKid ? 160 : 220}
          className="hidden md:table-cell"
        >
          {hideKid ? "Device" : "Kid · device"}
        </TableColumn>
        <TableColumn key="url">URL</TableColumn>
        <TableColumn key="rule" width={200} className="hidden lg:table-cell">
          Rule
        </TableColumn>
        <TableColumn key="action" width={110} align="end">
          {""}
        </TableColumn>
      </TableHeader>
      <TableBody items={events}>
        {(e) => (
          <TableRow key={`${e.id ?? "live"}-${e.ts}-${e.host}`}>
            <TableCell>
              <span className="font-mono text-xs text-default-500 whitespace-nowrap">
                {formatTime(e.ts)}
              </span>
            </TableCell>
            <TableCell>
              <Chip
                size="sm"
                color={decisionColor(e.decision)}
                variant="flat"
                className="capitalize"
              >
                {e.decision}
              </Chip>
            </TableCell>
            <TableCell className="hidden sm:table-cell">
              <span className="text-xs font-mono text-default-500">
                {e.kind ?? "—"}
              </span>
            </TableCell>
            <TableCell className="hidden md:table-cell">
              <Who event={e} hideKid={hideKid} />
            </TableCell>
            <TableCell>
              <div className="flex flex-col min-w-0">
                <div
                  className="font-mono text-xs truncate max-w-[28rem] lg:max-w-[40rem] xl:max-w-[60rem] 2xl:max-w-[80rem]"
                  title={`${e.host}${e.path ?? ""}${e.query ? `?${e.query}` : ""}`}
                >
                  {e.host}
                  {e.path}
                  {e.query ? <span className="text-default-400">?{e.query}</span> : null}
                </div>
                {/* Mobile: collapse Kid·device + Rule under the URL */}
                <div className="md:hidden text-[11px] text-default-500 mt-0.5 truncate">
                  <Who event={e} hideKid={hideKid} />
                  {e.rule && <span className="text-default-400"> · {e.rule}</span>}
                </div>
              </div>
            </TableCell>
            <TableCell className="hidden lg:table-cell">
              <span
                className="text-default-500 text-xs truncate max-w-[12rem] block"
                title={e.rule ?? ""}
              >
                {e.rule ?? "—"}
              </span>
            </TableCell>
            <TableCell>
              {e.kid ? (
                <Button
                  as={Link}
                  to="/kids/$name/rules/new"
                  params={{ name: e.kid }}
                  search={{
                    host: e.host,
                    path: e.path ?? "",
                    query: e.query ?? "",
                  }}
                  size="sm"
                  variant="light"
                >
                  Rule
                </Button>
              ) : (
                <span />
              )}
            </TableCell>
          </TableRow>
        )}
      </TableBody>
    </Table>
  );
}

function Who({ event: e, hideKid }: { event: Event; hideKid: boolean }) {
  const kidEl = e.kid ? (
    <Link
      to="/kids/$name"
      params={{ name: e.kid }}
      className="text-foreground hover:underline"
    >
      {e.kid}
    </Link>
  ) : (
    <span className="text-default-400">—</span>
  );
  const deviceEl =
    e.kid && e.client_ip ? (
      <Link
        to="/kids/$name/devices/$ip/enrol"
        params={{ name: e.kid, ip: e.client_ip }}
        className="text-default-500 hover:underline"
      >
        {e.device ?? e.client_ip}
      </Link>
    ) : (
      <span className="text-default-500">{e.device ?? e.client_ip}</span>
    );
  if (hideKid) return <span className="text-sm">{deviceEl}</span>;
  return (
    <span className="text-sm">
      {kidEl} <span className="text-default-400">·</span> {deviceEl}
    </span>
  );
}
