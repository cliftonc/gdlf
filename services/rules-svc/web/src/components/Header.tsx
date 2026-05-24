import { useState } from "react";
import {
  Navbar,
  NavbarBrand,
  NavbarContent,
  NavbarItem,
  NavbarMenu,
  NavbarMenuItem,
  NavbarMenuToggle,
  Link as HeroLink,
  Button,
} from "@heroui/react";
import { Link, useLocation, useNavigate } from "@tanstack/react-router";
import { ThemeToggle } from "./ThemeToggle";
import { useLogout } from "../lib/mutations";

const NAV: { to: string; label: string; match: string }[] = [
  { to: "/kids", label: "Kids", match: "/kids" },
  { to: "/activity", label: "Activity", match: "/activity" },
  { to: "/settings", label: "Settings", match: "/settings" },
];

export function Header() {
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  const nav = useNavigate();
  const logout = useLogout();

  const onLogout = async () => {
    await logout.mutateAsync();
    nav({ to: "/login" });
  };

  return (
    <Navbar
      isBordered
      maxWidth="full"
      isMenuOpen={open}
      onMenuOpenChange={setOpen}
      classNames={{ wrapper: "px-4 sm:px-6" }}
    >
      <NavbarContent className="md:hidden" justify="start">
        <NavbarMenuToggle aria-label={open ? "Close menu" : "Open menu"} />
      </NavbarContent>

      <NavbarBrand>
        <Link to="/kids" className="flex items-center gap-2 font-semibold">
          <img src="/logo-256.png" alt="" className="h-7 w-7 rounded" />
          <span className="text-base">gdlf</span>
        </Link>
      </NavbarBrand>

      <NavbarContent className="hidden md:flex gap-2" justify="center">
        {NAV.map((n) => (
          <NavbarItem key={n.to} isActive={loc.pathname.startsWith(n.match)}>
            <HeroLink as={Link} to={n.to} color="foreground" className="px-2">
              {n.label}
            </HeroLink>
          </NavbarItem>
        ))}
      </NavbarContent>

      <NavbarContent justify="end" className="gap-1">
        <NavbarItem>
          <ThemeToggle />
        </NavbarItem>
        <NavbarItem className="hidden md:flex">
          <Button size="sm" variant="flat" onPress={onLogout}>
            Sign out
          </Button>
        </NavbarItem>
      </NavbarContent>

      <NavbarMenu>
        {NAV.map((n) => (
          <NavbarMenuItem key={n.to} isActive={loc.pathname.startsWith(n.match)}>
            <HeroLink
              as={Link}
              to={n.to}
              size="lg"
              color="foreground"
              className="w-full"
              onPress={() => setOpen(false)}
            >
              {n.label}
            </HeroLink>
          </NavbarMenuItem>
        ))}
        <NavbarMenuItem>
          <HeroLink
            color="danger"
            size="lg"
            className="w-full cursor-pointer"
            onPress={() => {
              setOpen(false);
              onLogout();
            }}
          >
            Sign out
          </HeroLink>
        </NavbarMenuItem>
      </NavbarMenu>
    </Navbar>
  );
}
