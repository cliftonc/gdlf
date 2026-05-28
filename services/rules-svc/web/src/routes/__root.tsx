import { Outlet, createRootRoute, redirect, useRouterState } from "@tanstack/react-router";
import { Providers } from "../components/Providers";
import { Header } from "../components/Header";
import { api, ApiError } from "../lib/api";

export const Route = createRootRoute({
  beforeLoad: async ({ location }) => {
    if (location.pathname.startsWith("/login")) return;
    // Shortlink enrolment pages use code-only /api/dl/* endpoints; they
    // intentionally work without a parent session, so don't bounce to /login.
    if (location.pathname.startsWith("/dl/")) return;
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
  // Shortlink pages are shown to the kid on the device being enrolled. Drop
  // the parent nav (Kids / Activity / Settings / Logout) so they don't see
  // it (and don't get 401-redirected by clicking on it).
  const minimal = path.startsWith("/dl/");
  return (
    <Providers>
      {onLogin ? (
        <main className="min-h-screen flex items-center justify-center p-4 safe-pt safe-pb">
          <Outlet />
        </main>
      ) : minimal ? (
        <main className="min-h-screen w-full px-4 sm:px-6 lg:px-8 py-6 safe-pt safe-pb">
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
