import { createFileRoute } from "@tanstack/react-router";
import {
  Accordion,
  AccordionItem,
  Spinner,
} from "@heroui/react";
import { useLibrary } from "../lib/queries";
import { EmptyState } from "../components/EmptyState";

export const Route = createFileRoute("/rules")({
  component: RulesLibraryPage,
});

function RulesLibraryPage() {
  const lib = useLibrary();
  if (lib.isLoading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner />
      </div>
    );
  }
  if (!lib.data) return null;

  const blocklistNames = Object.keys(lib.data.blocklists);
  const appNames = Object.keys(lib.data.apps);

  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="text-2xl font-semibold mb-1">Rules library</h1>
        <p className="text-sm text-default-500">
          Read-only reference of blocklists and app definitions. Edit{" "}
          <code className="font-mono">config/kids.yaml</code> on the host to change.
        </p>
      </div>

      <section>
        <h2 className="text-lg font-semibold mb-3">Blocklists</h2>
        {blocklistNames.length === 0 ? (
          <EmptyState title="No blocklists defined" />
        ) : (
          <Accordion variant="splitted" selectionMode="multiple">
            {blocklistNames.map((name) => {
              const bl = lib.data!.blocklists[name];
              return (
                <AccordionItem
                  key={name}
                  title={name}
                  subtitle={bl.description || `${bl.sources.length} sources`}
                >
                  <ul className="text-sm font-mono space-y-1">
                    {bl.sources.map((s) => (
                      <li key={s} className="break-all">
                        {s}
                      </li>
                    ))}
                  </ul>
                </AccordionItem>
              );
            })}
          </Accordion>
        )}
      </section>

      <section>
        <h2 className="text-lg font-semibold mb-3">Apps</h2>
        {appNames.length === 0 ? (
          <EmptyState title="No apps defined" />
        ) : (
          <Accordion variant="splitted" selectionMode="multiple">
            {appNames.map((name) => {
              const app = lib.data!.apps[name];
              return (
                <AccordionItem
                  key={name}
                  title={name}
                  subtitle={`${app.hosts.length} hosts · ${app.ip_ranges.length} IP ranges`}
                >
                  <div className="grid sm:grid-cols-2 gap-4">
                    <div>
                      <p className="text-xs uppercase tracking-wide text-default-500 mb-1">
                        Hosts
                      </p>
                      <ul className="text-sm font-mono space-y-1">
                        {app.hosts.map((h) => (
                          <li key={h}>{h}</li>
                        ))}
                      </ul>
                    </div>
                    <div>
                      <p className="text-xs uppercase tracking-wide text-default-500 mb-1">
                        IP ranges
                      </p>
                      <ul className="text-sm font-mono space-y-1">
                        {app.ip_ranges.map((r) => (
                          <li key={r}>{r}</li>
                        ))}
                      </ul>
                    </div>
                  </div>
                </AccordionItem>
              );
            })}
          </Accordion>
        )}
      </section>
    </div>
  );
}
