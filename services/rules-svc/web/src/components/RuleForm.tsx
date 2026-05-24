import { useEffect, useState } from "react";
import {
  Button,
  Input,
  Select,
  SelectItem,
  Switch,
  Textarea,
} from "@heroui/react";
import { Link } from "@tanstack/react-router";
import { RuleActionEnum, type Rule, type RuleAction } from "../lib/schemas";

export type RuleFormValues = {
  action: RuleAction;
  match: string;
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
  suggestedMatch?: string;
  submitLabel: string;
  isPending: boolean;
  error: string | null;
  onSubmit: (values: RuleFormValues) => void;
  cancelTo: { search: { tab: "rules" } };
}) {
  const [action, setAction] = useState<RuleAction>(initial?.action ?? "block");
  const [match, setMatch] = useState(initial?.match ?? "");
  const [query, setQuery] = useState(initial?.query ?? "");
  const [flag, setFlag] = useState(initial?.flag ?? false);
  const [note, setNote] = useState(initial?.note ?? "");

  // Prefill `match` from a suggestion if the user hasn't typed yet
  // (only fires on create, since edit pre-fills from `initial`).
  useEffect(() => {
    if (!match && suggestedMatch) setMatch(suggestedMatch);
  }, [suggestedMatch, match]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({
      action,
      match: match.trim(),
      query: query.trim(),
      flag,
      note: note.trim(),
    });
  };

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
        label="Match (host[/path-glob])"
        value={match}
        onValueChange={setMatch}
        placeholder="youtube.com/shorts/*"
        isRequired
        description="Host portion supports * wildcards. Trailing /* matches any path."
      />
      <Input
        label="Query regex (optional)"
        value={query}
        onValueChange={setQuery}
        placeholder="(^|&)q=.*evil"
        description="Python regex applied to the raw query string. Use 'evil' to match the word, NOT '*evil*' (that's glob syntax)."
      />
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
          isDisabled={!match.trim()}
        >
          {submitLabel}
        </Button>
      </div>
    </form>
  );
}
