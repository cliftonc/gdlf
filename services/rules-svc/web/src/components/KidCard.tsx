import { Card, CardBody, Chip } from "@heroui/react";
import { Link } from "@tanstack/react-router";
import type { KidSummary } from "../lib/schemas";

export function KidCard({ kid }: { kid: KidSummary }) {
  const onBonus =
    kid.bonus_until && new Date(kid.bonus_until).getTime() > Date.now();

  return (
    <Link
      to="/kids/$name"
      params={{ name: kid.name }}
      className="block group rounded-large focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
    >
      <Card
        as="div"
        className="w-full h-full transition-colors group-hover:bg-content2 group-active:scale-[0.98]"
      >
        <CardBody className="gap-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-lg font-semibold leading-tight">{kid.name}</p>
              {kid.age !== null && (
                <p className="text-xs text-default-500">Age {kid.age}</p>
              )}
            </div>
            <div className="flex gap-1 flex-wrap justify-end">
              {kid.manual_block && (
                <Chip color="danger" size="sm" variant="flat">
                  Blocked
                </Chip>
              )}
              {onBonus && (
                <Chip color="warning" size="sm" variant="flat">
                  Bonus
                </Chip>
              )}
            </div>
          </div>
          <div className="flex gap-4 text-sm text-default-500">
            <span>
              <strong className="text-foreground">{kid.online_device_count}</strong>
              /{kid.device_count} online
            </span>
            <span>
              <strong className="text-foreground">{kid.rule_count}</strong> rules
            </span>
          </div>
          <div className="text-xs text-default-400 mt-1 truncate">
            wd {kid.schedule.weekday} · we {kid.schedule.weekend}
          </div>
        </CardBody>
      </Card>
    </Link>
  );
}
