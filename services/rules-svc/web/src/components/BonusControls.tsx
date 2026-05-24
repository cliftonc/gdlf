import { Button, Chip } from "@heroui/react";
import { useClearBonus, useGrantBonus } from "../lib/mutations";
import { useConfirm } from "../lib/hooks/useConfirm";

function formatUntil(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString();
}

export function BonusControls({ name, bonusUntil }: { name: string; bonusUntil: string | null }) {
  const grant = useGrantBonus(name);
  const clear = useClearBonus(name);
  const confirm = useConfirm();
  const active = bonusUntil && new Date(bonusUntil).getTime() > Date.now();

  const onClear = async () => {
    const ok = await confirm({
      title: "Clear bonus time?",
      body: "Schedule rules immediately resume.",
      confirmLabel: "Clear bonus",
      danger: true,
    });
    if (ok) clear.mutate();
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="text-sm text-default-500">
        {active ? (
          <span>
            Bonus active until{" "}
            <Chip size="sm" color="warning" variant="flat">
              {formatUntil(bonusUntil!)}
            </Chip>
          </span>
        ) : (
          <span>No bonus active.</span>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        {[15, 30, 60, 120].map((m) => (
          <Button
            key={m}
            size="sm"
            variant="flat"
            onPress={() => grant.mutate(m)}
            isDisabled={grant.isPending}
          >
            +{m} min
          </Button>
        ))}
        {active && (
          <Button size="sm" color="danger" variant="light" onPress={onClear}>
            Clear bonus
          </Button>
        )}
      </div>
    </div>
  );
}
