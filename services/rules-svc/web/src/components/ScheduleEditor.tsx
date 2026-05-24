import { useState } from "react";
import { Button, Input } from "@heroui/react";
import { useUpdateSchedule } from "../lib/mutations";
import type { Schedule } from "../lib/schemas";

export function ScheduleEditor({ name, schedule }: { name: string; schedule: Schedule }) {
  const [weekday, setWeekday] = useState(schedule.weekday);
  const [weekend, setWeekend] = useState(schedule.weekend);
  const upd = useUpdateSchedule(name);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const dirty = weekday !== schedule.weekday || weekend !== schedule.weekend;

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setMsg(null);
    setError(null);
    try {
      await upd.mutateAsync({ weekday, weekend });
      setMsg("Saved");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    }
  };

  return (
    <form onSubmit={save} className="flex flex-col gap-3 max-w-md">
      <Input
        label="Weekday allowed hours"
        value={weekday}
        onValueChange={setWeekday}
        description="HH:MM-HH:MM, comma-separate for multiple windows"
      />
      <Input
        label="Weekend allowed hours"
        value={weekend}
        onValueChange={setWeekend}
      />
      <div className="flex items-center gap-3">
        <Button
          type="submit"
          color="primary"
          isLoading={upd.isPending}
          isDisabled={!dirty}
        >
          Save schedule
        </Button>
        {msg && <span className="text-sm text-success">{msg}</span>}
        {error && <span className="text-sm text-danger">{error}</span>}
      </div>
    </form>
  );
}
