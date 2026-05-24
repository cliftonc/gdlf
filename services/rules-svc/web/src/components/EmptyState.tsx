import type { ReactNode } from "react";

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="text-center py-12 px-4 border border-dashed border-default-200 rounded-large">
      <p className="text-base font-medium mb-1">{title}</p>
      {body && <p className="text-sm text-default-500 mb-4">{body}</p>}
      {action}
    </div>
  );
}
