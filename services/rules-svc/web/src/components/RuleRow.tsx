import { Button, Chip } from "@heroui/react";
import { Link } from "@tanstack/react-router";
import type { Rule } from "../lib/schemas";
import { useDeleteRule, useMoveRule } from "../lib/mutations";
import { useConfirm } from "../lib/hooks/useConfirm";

function color(action: Rule["action"]) {
  return action === "block" ? "danger" : action === "allow" ? "success" : "warning";
}

export function RuleRow({
  kidName,
  rule,
  idx,
  total,
}: {
  kidName: string;
  rule: Rule;
  idx: number;
  total: number;
}) {
  const del = useDeleteRule(kidName);
  const move = useMoveRule(kidName);
  const confirm = useConfirm();

  const summary = rule.host + (rule.path ?? "");
  const onDelete = async () => {
    const ok = await confirm({
      title: "Delete rule?",
      body: summary,
      confirmLabel: "Delete rule",
      danger: true,
    });
    if (ok) del.mutate(idx);
  };

  return (
    <div className="flex flex-col sm:flex-row sm:items-center gap-3 p-4 border border-default-200 rounded-medium bg-content1">
      <div className="flex flex-col gap-1 min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <Chip size="sm" color={color(rule.action)} variant="flat">
            {rule.action}
          </Chip>
          {rule.flag && (
            <Chip size="sm" color="warning" variant="flat">
              flag
            </Chip>
          )}
          <span className="font-mono text-sm truncate">
            {rule.host}
            {rule.path && (
              <span className="text-default-500">{rule.path}</span>
            )}
          </span>
        </div>
        {rule.query && (
          <p className="text-xs text-default-500 font-mono truncate">query: {rule.query}</p>
        )}
        {rule.note && <p className="text-xs text-default-500">{rule.note}</p>}
      </div>
      <div className="flex items-center gap-1">
        <Button
          size="sm"
          isIconOnly
          variant="light"
          isDisabled={idx === 0}
          onPress={() => move.mutate({ idx, dir: "up" })}
          aria-label="Move up"
        >
          ↑
        </Button>
        <Button
          size="sm"
          isIconOnly
          variant="light"
          isDisabled={idx === total - 1}
          onPress={() => move.mutate({ idx, dir: "down" })}
          aria-label="Move down"
        >
          ↓
        </Button>
        <Button
          as={Link}
          to="/kids/$name/rules/$idx/edit"
          params={{ name: kidName, idx: String(idx) }}
          size="sm"
          variant="flat"
        >
          Edit
        </Button>
        <Button size="sm" color="danger" variant="light" onPress={onDelete}>
          Delete
        </Button>
      </div>
    </div>
  );
}
