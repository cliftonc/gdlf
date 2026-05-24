import { useState } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Button, Card, CardBody, CardHeader, Input } from "@heroui/react";
import { useLogin } from "../lib/mutations";
import { ApiError } from "../lib/api";
import { z } from "zod";

const search = z.object({ next: z.string().optional() });

export const Route = createFileRoute("/login")({
  validateSearch: search,
  component: LoginPage,
});

function LoginPage() {
  const { next } = Route.useSearch();
  const nav = useNavigate();
  const login = useLogin();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await login.mutateAsync(password);
      const dest = next && next.startsWith("/") && !next.startsWith("//") ? next : "/kids";
      nav({ to: dest });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setError("Incorrect password");
      } else {
        setError(e instanceof Error ? e.message : "Login failed");
      }
    }
  };

  return (
    <Card className="w-full max-w-sm">
      <CardHeader className="flex flex-col items-center gap-2 pt-8">
        <img src="/logo-256.png" alt="" className="h-14 w-14 rounded-xl" />
        <h1 className="text-xl font-semibold">gdlf</h1>
        <p className="text-sm text-default-500">Parental controls</p>
      </CardHeader>
      <CardBody>
        <form onSubmit={onSubmit} className="flex flex-col gap-4 px-2 pb-4">
          <Input
            type="password"
            label="Admin password"
            value={password}
            onValueChange={setPassword}
            autoFocus
            isRequired
            errorMessage={error ?? undefined}
            isInvalid={!!error}
          />
          <Button
            type="submit"
            color="primary"
            isLoading={login.isPending}
            isDisabled={!password}
          >
            Sign in
          </Button>
        </form>
      </CardBody>
    </Card>
  );
}
