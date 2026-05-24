import { HeroUIProvider } from "@heroui/system";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider as NextThemesProvider } from "next-themes";
import { useNavigate } from "@tanstack/react-router";
import { type ReactNode, useState } from "react";
import { ConfirmProvider } from "../lib/hooks/useConfirm";

declare module "@react-types/shared" {
  interface RouterConfig {
    href: string;
  }
}

export function Providers({ children }: { children: ReactNode }) {
  const nav = useNavigate();
  const [qc] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: (count, err: unknown) => {
              if (typeof err === "object" && err && "status" in err && (err as { status: number }).status === 401) {
                return false;
              }
              return count < 2;
            },
            refetchOnWindowFocus: false,
            staleTime: 30_000,
          },
        },
      })
  );

  return (
    <HeroUIProvider
      navigate={(to: string) => nav({ to })}
      useHref={(to: string) => to}
    >
      <NextThemesProvider attribute="class" defaultTheme="system" enableSystem>
        <QueryClientProvider client={qc}>
          <ConfirmProvider>{children}</ConfirmProvider>
        </QueryClientProvider>
      </NextThemesProvider>
    </HeroUIProvider>
  );
}
