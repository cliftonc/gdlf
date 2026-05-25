import { useState } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Card, CardBody, Spinner } from "@heroui/react";
import { useKid } from "../lib/queries";
import { useUpdateRule } from "../lib/mutations";
import { RuleForm } from "../components/RuleForm";

export const Route = createFileRoute("/kids/$name/rules/$idx/edit")({
  component: RuleEditPage,
});

function RuleEditPage() {
  const { name, idx } = Route.useParams();
  const idxNum = parseInt(idx, 10);
  const nav = useNavigate();
  const kid = useKid(name);
  const upd = useUpdateRule(name);
  const [error, setError] = useState<string | null>(null);

  if (kid.isLoading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner />
      </div>
    );
  }
  if (!kid.data) return null;
  const rule = kid.data.rules[idxNum];
  if (!rule) {
    return (
      <p className="text-danger">
        Rule {idxNum} not found on {name}.
      </p>
    );
  }

  return (
    <div className="max-w-xl mx-auto">
      <Link
        to="/kids/$name"
        params={{ name }}
        search={{ tab: "rules" }}
        className="text-sm text-default-500 hover:underline"
      >
        ← {name}
      </Link>
      <h1 className="text-2xl font-semibold mt-1 mb-4">Edit rule</h1>
      <Card>
        <CardBody>
          <RuleForm
            name={name}
            initial={rule}
            submitLabel="Save rule"
            isPending={upd.isPending}
            error={error}
            cancelTo={{ search: { tab: "rules" } }}
            onSubmit={async (values) => {
              setError(null);
              try {
                await upd.mutateAsync({
                  idx: idxNum,
                  body: {
                    action: values.action,
                    host: values.host,
                    path: values.path || null,
                    query: values.query || null,
                    flag: values.flag,
                    note: values.note || null,
                  },
                });
                nav({
                  to: "/kids/$name",
                  params: { name },
                  search: { tab: "rules" },
                });
              } catch (e) {
                setError(e instanceof Error ? e.message : "Failed to save rule");
              }
            }}
          />
        </CardBody>
      </Card>
    </div>
  );
}
