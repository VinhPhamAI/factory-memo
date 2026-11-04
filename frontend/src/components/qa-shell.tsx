import { useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import {
  Files,
  FolderPlus,
  LibraryBig,
  LogOut,
  Menu,
  MessagesSquare,
} from "lucide-react";
import { BrandMark } from "./brand";
import { ConversationList } from "./conversation-list";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useWorkspace } from "@/hooks/workspace";
import { useAuth, useAuthClient } from "@/hooks/auth";
import { ConversationsProvider } from "@/hooks/conversations";
import { AuthError } from "@/services/auth-api";
import { cn } from "@/utils/cn";

const SECTIONS = [
  { to: "/ask", label: "Hỏi đáp", icon: MessagesSquare, testId: "nav-ask" },
  {
    to: "/capture",
    label: "Thêm dữ liệu",
    icon: FolderPlus,
    testId: "nav-capture",
  },
  {
    to: "/knowledge",
    label: "Quản lý tri thức",
    icon: LibraryBig,
    testId: "nav-knowledge-management",
  },
  { to: "/files", label: "Thư viện file", icon: Files, testId: "nav-files" },
];

/** Sidebar content: brand, sections, the chat list on /ask, and the account. */
function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const auth = useAuth();
  const authClient = useAuthClient();
  const { pathname } = useLocation();
  const [logoutError, setLogoutError] = useState("");
  return (
    <div className="sidebar-content">
      <Link to="/ask" className="sidebar-brand" onClick={onNavigate}>
        <BrandMark small />
        <span>Factory memo</span>
      </Link>
      <nav className="sidebar-nav" aria-label="Chức năng">
        {SECTIONS.map(({ to, label, icon: Icon, testId }) => (
          <NavLink
            key={to}
            to={to}
            data-testid={testId}
            onClick={onNavigate}
            className={({ isActive }) =>
              cn("sidebar-link", isActive && "active")
            }
          >
            <Icon size={16} />
            {label}
          </NavLink>
        ))}
      </nav>
      <div
        className={cn(
          "sidebar-scroll",
          pathname.startsWith("/ask") && "has-list",
        )}
      >
        {pathname.startsWith("/ask") && (
          <ConversationList onNavigate={onNavigate} />
        )}
      </div>
      {auth.status === "signed-in" && (
        <div className="sidebar-account">
          <div
            className="sidebar-user"
            title={auth.user.username}
            data-testid="signed-in-user"
          >
            <span className="sidebar-avatar" aria-hidden="true">
              {auth.user.display_name.slice(0, 1).toUpperCase()}
            </span>
            <span>
              <strong>{auth.user.display_name}</strong>
              <small>@{auth.user.username}</small>
            </span>
          </div>
          <button
            type="button"
            className="sidebar-link"
            data-testid="sign-out"
            onClick={() => {
              setLogoutError("");
              authClient
                .logout()
                .catch((error: unknown) =>
                  setLogoutError(
                    error instanceof AuthError
                      ? error.message
                      : "Không đăng xuất được, vui lòng thử lại.",
                  ),
                );
            }}
          >
            <LogOut size={16} />
            Đăng xuất
          </button>
          {logoutError && (
            <p role="alert" className="inline-error sidebar-error">
              {logoutError}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/** Live mode: a left sidebar (drawer on phones) next to the current page. */
export function QaShell() {
  const workspace = useWorkspace();
  const [drawerOpen, setDrawerOpen] = useState(false);
  return (
    <ConversationsProvider>
      <div className="app-shell chat-focus-shell" data-testid="chat-focus-root">
        <aside className="app-sidebar">
          <Sidebar />
        </aside>
        <div className="app-main">
          <header className="app-mobile-bar">
            <button
              type="button"
              aria-label="Mở menu"
              data-testid="open-menu"
              onClick={() => setDrawerOpen(true)}
            >
              <Menu size={20} />
            </button>
            <Link to="/ask" className="sidebar-brand">
              <BrandMark small />
              <span>Factory memo</span>
            </Link>
          </header>
          {workspace.error && (
            <div className="workspace-error" role="alert">
              {workspace.error}
            </div>
          )}
          <div className="workspace-outlet">
            <Outlet />
          </div>
        </div>
        <Sheet open={drawerOpen} onOpenChange={setDrawerOpen}>
          <SheetContent side="left" className="app-drawer">
            <SheetHeader className="sr-only">
              <SheetTitle>Menu</SheetTitle>
              <SheetDescription>Chức năng và cuộc trò chuyện.</SheetDescription>
            </SheetHeader>
            <Sidebar onNavigate={() => setDrawerOpen(false)} />
          </SheetContent>
        </Sheet>
      </div>
    </ConversationsProvider>
  );
}
