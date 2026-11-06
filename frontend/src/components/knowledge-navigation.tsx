import { useId, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import {
  ChevronDown,
  Database,
  Files,
  LibraryBig,
} from "lucide-react";
import { cn } from "@/utils/cn";

const VIEWS = [
  { value: "knowledge-bases", label: "Knowledge base", icon: Database },
  { value: "sources", label: "Nguồn dữ liệu", icon: Files },
];

/** Expand knowledge links in the main sidebar and retain URL query settings. */
export function KnowledgeNavigation({
  disabled = false,
  onNavigate,
}: {
  disabled?: boolean;
  onNavigate?: () => void;
}) {
  const { pathname, search } = useLocation();
  const active =
    pathname === "/knowledge" || pathname.startsWith("/knowledge/");
  // Reset the disclosure when entering a different section, including browser history.
  const [disclosure, setDisclosure] = useState({ pathname, open: active });
  if (disclosure.pathname !== pathname) {
    setDisclosure({ pathname, open: active });
  }
  const id = useId();
  const params = new URLSearchParams(search);
  const tab = params.get("tab");
  const view = VIEWS.some(({ value }) => value === tab) ? tab : "sources";

  return (
    <div className="sidebar-knowledge">
      <button
        type="button"
        className={cn("sidebar-link", active && "active")}
        data-testid="nav-knowledge-management"
        aria-expanded={disclosure.open}
        aria-controls={id}
        onClick={() => setDisclosure({ pathname, open: !disclosure.open })}
      >
        <LibraryBig size={16} aria-hidden="true" />
        Quản lý tri thức
        <ChevronDown className="sidebar-chevron" size={14} aria-hidden="true" />
      </button>
      <div id={id} className="sidebar-subnav" hidden={!disclosure.open}>
        {VIEWS.map(({ value, label, icon: Icon }) => {
          const nextParams = new URLSearchParams(active ? search : "");
          if (value === "sources") nextParams.delete("tab");
          else nextParams.set("tab", value);
          const selected =
            active &&
            (pathname !== "/knowledge" ? value === "sources" : view === value);
          return (
            <Link
              key={value}
              to={{ pathname: "/knowledge", search: nextParams.toString() }}
              className={cn("sidebar-link", selected && "active")}
              aria-current={selected ? "page" : undefined}
              aria-disabled={disabled || undefined}
              onClick={(event) => {
                if (disabled) event.preventDefault();
                else onNavigate?.();
              }}
            >
              <Icon size={15} aria-hidden="true" />
              {label}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
