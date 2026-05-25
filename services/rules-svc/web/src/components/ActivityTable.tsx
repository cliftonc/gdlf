import { Link } from "@tanstack/react-router";
import {
  Button,
  Table,
  TableBody,
  TableCell,
  TableColumn,
  TableHeader,
  TableRow,
  Tooltip,
} from "@heroui/react";
import type { Event } from "../lib/schemas";

// Dot color → tailwind bg class. Kept off HeroUI's Chip palette so the dot
// is a fixed solid colour (Chip variants pull in border/text styling).
function decisionDotClass(decision: string): string {
  if (decision === "block" || decision === "dns_block") return "bg-danger";
  if (decision === "flag" || decision === "tls_failed") return "bg-warning";
  if (decision === "passthrough") return "bg-primary";
  if (decision === "sni_only") return "bg-default-400";
  return "bg-success";
}

function decisionLabel(decision: string) {
  if (decision === "tls_failed") return "TLS failed";
  if (decision === "passthrough") return "Passthrough";
  return decision;
}

function formatTime(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString([], { hour12: false });
}

// The server collapses same-bucket repeats into one row with hit_count > 1.
// Render that as "×N" next to the URL so the parent can see at a glance
// how many real hits a single visible row represents — the counter tiles
// add up to SUM(hit_count) over the visible window, so this is the same
// number the overview is counting.
function hitCountLabel(n: number): string | null {
  if (!n || n <= 1) return null;
  return `×${n}`;
}

// Compound second-level public suffixes that appear in kid browsing often
// enough to be worth special-casing. A full Public Suffix List (~50 KB)
// would be more correct but the wrong-bolding for the long tail is purely
// cosmetic, so we lean lightweight.
const COMPOUND_SUFFIXES = new Set([
  "co.uk", "ac.uk", "gov.uk", "org.uk", "co.jp", "ne.jp",
  "com.au", "net.au", "org.au", "co.nz", "co.za", "com.br",
  "com.cn", "com.mx", "com.tw", "co.kr", "com.sg", "com.hk",
  "co.in", "co.id", "com.tr", "com.ph", "com.my", "com.ar",
]);

// Split a hostname into [subdomainPrefix, registrable]. The registrable
// (eTLD+1) is what we bold. IPs and single-label hosts return ["", host].
function splitHost(host: string): [string, string] {
  if (!host) return ["", ""];
  // Strip port if any
  const hostNoPort = host.replace(/:\d+$/, "");
  // IPv4 / IPv6 — don't try to find a registrable, just bold the whole thing
  if (/^\d+\.\d+\.\d+\.\d+$/.test(hostNoPort) || hostNoPort.includes(":")) {
    return ["", hostNoPort];
  }
  const labels = hostNoPort.split(".");
  if (labels.length <= 2) return ["", hostNoPort];
  const lastTwo = labels.slice(-2).join(".");
  const takeThree = COMPOUND_SUFFIXES.has(lastTwo) && labels.length >= 3;
  const registrable = labels.slice(takeThree ? -3 : -2).join(".");
  const prefix = labels.slice(0, takeThree ? -3 : -2).join(".") + ".";
  return [prefix, registrable];
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
      removeWrapper
      aria-label="Activity events"
      classNames={{
        base: "overflow-x-auto",
        // table-fixed makes the URL column share the *remaining* width
        // after the explicitly-sized columns. Without it the column
        // auto-grows to fit the longest URL and pushes the table wide.
        table: "min-w-[560px] w-full table-fixed",
        th: "text-[11px] uppercase tracking-wide h-7 py-0",
        td: "py-0.5 align-middle leading-tight",
      }}
    >
      <TableHeader>
        <TableColumn key="time" width={70}>
          Time
        </TableColumn>
        <TableColumn key="decision" width={20} align="center">
          {""}
        </TableColumn>
        <TableColumn key="kind" width={60} className="hidden sm:table-cell">
          Kind
        </TableColumn>
        <TableColumn
          key="who"
          width={hideKid ? 130 : 180}
          className="hidden md:table-cell"
        >
          {hideKid ? "Device" : "Kid · device"}
        </TableColumn>
        <TableColumn key="url">URL</TableColumn>
        <TableColumn key="rule" width={160} className="hidden lg:table-cell">
          Rule
        </TableColumn>
        <TableColumn key="action" width={70} align="end">
          {""}
        </TableColumn>
      </TableHeader>
      <TableBody items={events}>
        {(e) => (
          <TableRow key={`${e.id ?? "live"}-${e.ts_last ?? e.ts}-${e.host}`}>
            <TableCell>
              <span className="font-mono text-[11px] text-default-500 whitespace-nowrap">
                {formatTime(e.ts_last ?? e.ts)}
              </span>
            </TableCell>
            <TableCell>
              <Tooltip content={decisionLabel(e.decision)} placement="right" delay={300}>
                <span
                  aria-label={decisionLabel(e.decision)}
                  className={`inline-block w-2 h-2 rounded-full ${decisionDotClass(e.decision)}`}
                />
              </Tooltip>
            </TableCell>
            <TableCell className="hidden sm:table-cell">
              <span className="text-[11px] font-mono text-default-500">
                {e.kind ?? "—"}
              </span>
            </TableCell>
            <TableCell className="hidden md:table-cell">
              <Who event={e} hideKid={hideKid} />
            </TableCell>
            <TableCell>
              {/* min-w-0 lets the truncating child shrink inside the
                  fixed-layout table column. The link itself uses `block
                  truncate` so it ellipsises at whatever width the column
                  ended up with. Clicking opens host+path in a new tab
                  (we drop the query — it's just for context). */}
              <div className="flex flex-col min-w-0">
                <HostUrl event={e} />
                {/* Mobile: collapse Kid·device + Rule under the URL */}
                <div className="md:hidden text-[10px] text-default-500 truncate">
                  <Who event={e} hideKid={hideKid} />
                  {e.rule && (
                    <span className="text-default-400"> · {e.rule}</span>
                  )}
                </div>
              </div>
            </TableCell>
            <TableCell className="hidden lg:table-cell">
              <span
                className="text-default-500 text-[11px] truncate block"
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
                  className="h-6 min-w-0 px-2 text-xs"
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

function HostUrl({ event: e }: { event: Event }) {
  const [prefix, registrable] = splitHost(e.host);
  const titleFull = `${e.host}${e.path ?? ""}${e.query ? `?${e.query}` : ""}`;
  const hits = hitCountLabel(e.hit_count);
  return (
    <a
      href={`https://${e.host}${e.path ?? ""}`}
      target="_blank"
      rel="noreferrer"
      className="block truncate font-mono text-[11px] text-foreground hover:text-primary hover:underline"
      title={titleFull}
    >
      {prefix && <span className="text-default-500">{prefix}</span>}
      <span className="font-semibold">{registrable}</span>
      {e.path}
      {e.query ? <span className="text-default-300">?{e.query}</span> : null}
      {hits && (
        <span className="ml-2 text-default-400 font-sans">{hits}</span>
      )}
    </a>
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
  if (hideKid) return <span className="text-xs">{deviceEl}</span>;
  return (
    <span className="text-xs">
      {kidEl} <span className="text-default-400">·</span> {deviceEl}
    </span>
  );
}
