import { useState } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Card, CardBody, CardHeader } from "@heroui/react";
import { z } from "zod";
import { useRuleSuggest } from "../lib/queries";
import { useAddRule } from "../lib/mutations";
import { RuleForm } from "../components/RuleForm";

const search = z.object({
  host: z.string().optional(),
  path: z.string().optional(),
  query: z.string().optional(),
});

export const Route = createFileRoute("/kids/$name/rules/new")({
  validateSearch: search,
  component: RuleNewPage,
});

function RuleNewPage() {
  const { name } = Route.useParams();
  const sr = Route.useSearch();
  const nav = useNavigate();
  const add = useAddRule(name);
  const suggest = useRuleSuggest(sr.host ?? "", sr.path ?? "", !!(sr.host || sr.path));
  const [error, setError] = useState<string | null>(null);

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
      <h1 className="text-2xl font-semibold mt-1 mb-4">New rule</h1>
      <Card>
        {(sr.host || sr.path) && (
          <CardHeader>
            <p className="text-xs text-default-500">
              Suggested from{" "}
              <span className="font-mono">
                {sr.host}
                {sr.path}
              </span>
            </p>
          </CardHeader>
        )}
        <CardBody>
          <RuleForm
            name={name}
            suggestedMatch={suggest.data}
            initial={
              sr.query
                ? {
                    action: "block",
                    host: "",
                    path: null,
                    query: sr.query,
                    flag: false,
                    note: null,
                  }
                : undefined
            }
            submitLabel="Add rule"
            isPending={add.isPending}
            error={error}
            cancelTo={{ search: { tab: "rules" } }}
            onSubmit={async (values) => {
              setError(null);
              try {
                await add.mutateAsync({
                  action: values.action,
                  host: values.host,
                  path: values.path || null,
                  query: values.query || null,
                  flag: values.flag,
                  note: values.note || null,
                });
                nav({
                  to: "/kids/$name",
                  params: { name },
                  search: { tab: "rules" },
                });
              } catch (e) {
                setError(e instanceof Error ? e.message : "Failed to add rule");
              }
            }}
          />
        </CardBody>
      </Card>
    </div>
  );
}
