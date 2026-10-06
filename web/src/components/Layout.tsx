import { useQueryClient } from "@tanstack/react-query";
import { Moon, Radio, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { post } from "../api/client";
import type { User } from "../api/types";
import { useLiveEvents } from "../lib/useLiveEvents";
import { Button, cx } from "./ui";

function useTheme(): [string, () => void] {
  const [theme, setTheme] = useState<string>(() => {
    try {
      return localStorage.getItem("theme") ?? "system";
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    if (theme === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("theme", theme);
    } catch {
      /* private mode: theme just won't persist */
    }
  }, [theme]);
  const dark =
    theme === "dark" ||
    (theme === "system" && window.matchMedia?.("(prefers-color-scheme: dark)").matches);
  return [dark ? "dark" : "light", () => setTheme(dark ? "light" : "dark")];
}

export function Layout({ user }: { user: User }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const live = useLiveEvents();
  const [theme, toggleTheme] = useTheme();
  const links = [
    { to: "/queue", label: "Queue", roles: ["intake", "radiologist", "admin"] },
    { to: "/upload", label: "Upload", roles: ["intake", "admin"] },
    { to: "/eval", label: "Evaluation", roles: ["intake", "radiologist", "admin"] },
    { to: "/protocols", label: "Protocols", roles: ["intake", "radiologist", "admin"] },
    { to: "/audit", label: "Audit trail", roles: ["admin"] },
    { to: "/ops", label: "Operations", roles: ["admin"] },
  ].filter((l) => l.roles.includes(user.role));

  async function logout() {
    await post("/auth/logout");
    client.clear();
    navigate("/login");
  }

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-line bg-surface/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2.5">
          <div className="flex items-baseline gap-2">
            <span className="text-base font-bold">IntakeCopilot</span>
            <span className="hidden text-xs text-ink-2 sm:inline">Lakeshore MRI &amp; CT · synthetic data</span>
          </div>
          <nav className="flex flex-wrap gap-1" aria-label="Main">
            {links.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                className={({ isActive }) =>
                  cx(
                    "rounded-md px-2.5 py-1 text-sm",
                    isActive ? "bg-surface-2 font-semibold text-ink" : "text-ink-2 hover:text-ink",
                  )
                }
              >
                {link.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <span
              className="inline-flex items-center gap-1 text-xs text-ink-2"
              title={live ? "Live updates connected" : "Live updates reconnecting"}
            >
              <Radio className="size-3.5" style={{ color: live ? "var(--good)" : "var(--muted)" }} aria-hidden />
              {live ? "Live" : "Offline"}
            </span>
            <button
              onClick={toggleTheme}
              className="rounded p-1 text-ink-2 hover:bg-surface-2"
              aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
            >
              {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </button>
            <span className="text-ink-2">{user.display_name}</span>
            <Button variant="ghost" onClick={logout}>
              Sign out
            </Button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1500px] px-4 py-5">
        <Outlet />
      </main>
    </div>
  );
}
