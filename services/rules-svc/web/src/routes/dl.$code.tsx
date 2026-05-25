import { createFileRoute } from "@tanstack/react-router";
import { Spinner } from "@heroui/react";
import { useResolveDl } from "../lib/queries";
import { EnrolView } from "./kids.$name.devices.$ip.enrol";

export const Route = createFileRoute("/dl/$code")({
  component: ShortlinkLandingPage,
});

function ShortlinkLandingPage() {
  const { code } = Route.useParams();
  const resolved = useResolveDl(code);

  if (resolved.isLoading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner />
      </div>
    );
  }
  if (resolved.error || !resolved.data) {
    return (
      <div className="max-w-3xl mx-auto py-16 text-center">
        <h1 className="text-2xl font-semibold mb-2">Link not found</h1>
        <p className="text-sm text-default-500">
          This enrolment link has been revoked, rotated, or never existed. Ask the parent to
          generate a fresh one.
        </p>
      </div>
    );
  }

  const { kid, ip } = resolved.data;
  return <EnrolView name={kid} ip={ip} dlCode={code} />;
}
