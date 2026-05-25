import { useEffect, useMemo, useState } from "react";
import {
  Button,
  Input,
  Select,
  SelectItem,
  Switch,
  Textarea,
} from "@heroui/react";
import { Link } from "@tanstack/react-router";
import { useKid } from "../lib/queries";
import { RuleActionEnum, type Rule, type RuleAction } from "../lib/schemas";

// Reuse the same host-glob matcher logic as the backend (fnmatch + bare-host
// subdomain extension) so the editor can tell the parent up-front whether
// their path filter will actually fire.
function hostMatchesAny(host: string, patterns: string[]): boolean {
  const h = host.toLowerCase();
  for (const raw of patterns) {
    const p = raw.toLowerCase();
    if (!p) continue;
    if (p === h) return true;
    // glob → regex (fnmatch subset: * and ?)
    const re = new RegExp(
      "^" +
        p
          .replace(/[.+^${}()|[\]\\]/g, "\\$&")
          .replace(/\*/g, ".*")
          .replace(/\?/g, ".") +
        "$",
    );
    if (re.test(h)) return true;
    // bare "example.com" matches subdomains
    if (!p.includes("*") && h.endsWith("." + p)) return true;
  }
  return false;
}

export type RuleFormValues = {
  action: RuleAction;
  host: string;
  path: string;
  query: string;
  flag: boolean;
  note: string;
};

export function RuleForm({
  name,
  initial,
  suggestedMatch,
  submitLabel,
  isPending,
  error,
  onSubmit,
  cancelTo,
}: {
  name: string;
  initial?: Rule;
  suggestedMatch?: { host: string; path: string | null };
  submitLabel: string;
  isPending: boolean;
  error: string | null;
  onSubmit: (values: RuleFormValues) => void;
  cancelTo: { search: { tab: "rules" } };
}) {
  const [action, setAction] = useState<RuleAction>(initial?.action ?? "block");
  const [host, setHost] = useState(initial?.host ?? "");
  const [path, setPath] = useState(initial?.path ?? "");
  const [query, setQuery] = useState(initial?.query ?? "");
  const [flag, setFlag] = useState(initial?.flag ?? false);
  const [note, setNote] = useState(initial?.note ?? "");

  // Prefill from a suggestion if the user hasn't typed yet (create only —
  // edit pre-fills from `initial`).
  useEffect(() => {
    if (!host && suggestedMatch?.host) setHost(suggestedMatch.host);
    if (!path && suggestedMatch?.path) setPath(suggestedMatch.path);
  }, [suggestedMatch, host, path]);

  // Whether the host (as currently typed) is on the kid's MITM list.
  // Determines whether the path/query filters will actually fire.
  const kid = useKid(name);
  const hostIsMitm = useMemo(() => {
    if (!host.trim() || !kid.data) return false;
    return hostMatchesAny(host.trim(), kid.data.mitm_inspect_hosts);
  }, [host, kid.data]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({
      action,
      host: host.trim(),
      path: path.trim(),
      query: query.trim(),
      flag,
      note: note.trim(),
    });
  };

  const pathInUse = path.trim().length > 0 || query.trim().length > 0;

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <Select
        label="Action"
        selectedKeys={new Set([action])}
        onSelectionChange={(keys) => {
          const k = Array.from(keys as Set<string>)[0];
          if (k) setAction(RuleActionEnum.parse(k));
        }}
      >
        <SelectItem key="block">block</SelectItem>
        <SelectItem key="allow">allow</SelectItem>
        <SelectItem key="flag">flag (allow + alert)</SelectItem>
      </Select>
      <Input
        label="Host"
        value={host}
        onValueChange={setHost}
        placeholder="youtube.com or *.reddit.com"
        isRequired
        description="Glob applied to the SNI / Host header. Bare `example.com` also matches `*.example.com`."
      />
      <Input
        label="Path glob (MITM only)"
        value={path}
        onValueChange={setPath}
        placeholder="/shorts/*"
        description="Optional. Only enforced when the host is on this kid's MITM list — otherwise the rule matches on host alone."
      />
      <Input
        label="Query regex (MITM only)"
        value={query}
        onValueChange={setQuery}
        placeholder="(^|&)q=.*evil"
        description="Optional Python regex on the raw query string. MITM-only, same as path."
      />
      {pathInUse && !hostIsMitm && host.trim() && kid.data && (
        <p className="text-xs text-warning-600">
          Heads-up: <span className="font-mono">{host.trim()}</span> isn't on{" "}
          {name}'s MITM list, so the path/query filter will be ignored — the
          rule matches the host alone. Add the host to the MITM tab to get
          path-level enforcement.
        </p>
      )}
      <Switch isSelected={flag} onValueChange={setFlag}>
        Also alert parents on match
      </Switch>
      <Textarea
        label="Note (optional)"
        value={note}
        onValueChange={setNote}
        minRows={2}
      />
      {error && <p className="text-danger text-sm">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button
          as={Link}
          to="/kids/$name"
          params={{ name }}
          search={cancelTo.search}
          variant="light"
        >
          Cancel
        </Button>
        <Button
          type="submit"
          color="primary"
          isLoading={isPending}
          isDisabled={!host.trim()}
        >
          {submitLabel}
        </Button>
      </div>
    </form>
  );
}
