import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import {
  Button,
  Input,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
  Spinner,
} from "@heroui/react";
import { useKids } from "../lib/queries";
import { useCreateKid } from "../lib/mutations";
import { KidCard } from "../components/KidCard";
import { EmptyState } from "../components/EmptyState";

export const Route = createFileRoute("/kids/")({
  component: KidsIndex,
});

function KidsIndex() {
  const kids = useKids();
  const [open, setOpen] = useState(false);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Kids</h1>
        <Button color="primary" onPress={() => setOpen(true)}>
          Add kid
        </Button>
      </div>

      {kids.isLoading && (
        <div className="flex justify-center py-12">
          <Spinner />
        </div>
      )}

      {kids.error && (
        <p className="text-danger">Failed to load kids: {String(kids.error)}</p>
      )}

      {kids.data && kids.data.length === 0 && (
        <EmptyState
          title="No kids yet"
          body="Add your first kid to start enrolling devices."
          action={
            <Button color="primary" onPress={() => setOpen(true)}>
              Add kid
            </Button>
          }
        />
      )}

      {kids.data && kids.data.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-4">
          {kids.data.map((k) => (
            <KidCard key={k.name} kid={k} />
          ))}
        </div>
      )}

      <AddKidModal open={open} onClose={() => setOpen(false)} />
    </div>
  );
}

function AddKidModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const create = useCreateKid();
  const [name, setName] = useState("");
  const [age, setAge] = useState("");
  const [weekday, setWeekday] = useState("07:00-21:00");
  const [weekend, setWeekend] = useState("08:00-22:00");
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setName("");
    setAge("");
    setWeekday("07:00-21:00");
    setWeekend("08:00-22:00");
    setError(null);
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await create.mutateAsync({
        name: name.trim(),
        age: age ? parseInt(age, 10) : null,
        schedule_weekday: weekday,
        schedule_weekend: weekend,
      });
      reset();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add kid");
    }
  };

  return (
    <Modal
      isOpen={open}
      onOpenChange={(o) => {
        if (!o) {
          reset();
          onClose();
        }
      }}
      placement="center"
      size="md"
    >
      <ModalContent>
        <form onSubmit={submit}>
          <ModalHeader>Add a kid</ModalHeader>
          <ModalBody className="gap-3">
            <Input
              label="Name"
              value={name}
              onValueChange={setName}
              isRequired
              autoFocus
            />
            <Input
              label="Age (optional)"
              type="number"
              value={age}
              onValueChange={setAge}
              min={1}
              max={25}
            />
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
            {error && <p className="text-danger text-sm">{error}</p>}
          </ModalBody>
          <ModalFooter>
            <Button variant="light" onPress={onClose}>
              Cancel
            </Button>
            <Button
              type="submit"
              color="primary"
              isLoading={create.isPending}
              isDisabled={!name.trim()}
            >
              Add kid
            </Button>
          </ModalFooter>
        </form>
      </ModalContent>
    </Modal>
  );
}
