import { Outlet, createRootRoute, redirect, useRouterState } from "@tanstack/react-router";
import { Providers } from "../components/Providers";
import { Header } from "../components/Header";
import { api, ApiError } from "../lib/api";

export const Route = createRootRoute({
  beforeLoad: async ({ location }) => {
    if (location.pathname.startsWith("/login")) return;
    try {
      await api("/api/auth/me");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        throw redirect({ to: "/login", search: { next: location.href } });
      }
      throw e;
    }
  },
  component: RootLayout,
});

function RootLayout() {
  const path = useRouterState({ select: (s) => s.location.pathname });
  const onLogin = path.startsWith("/login");
  return (
    <Providers>
      {onLogin ? (
        <main className="min-h-screen flex items-center justify-center p-4 safe-pt safe-pb">
          <Outlet />
        </main>
      ) : (
        <div className="min-h-screen flex flex-col safe-pt safe-pb">
          <Header />
          <main className="flex-1 w-full px-4 sm:px-6 lg:px-8 py-6">
            <Outlet />
          </main>
        </div>
      )}
    </Providers>
  );
}
