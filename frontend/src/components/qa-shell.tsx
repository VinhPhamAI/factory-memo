import { useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import {
  BookOpen,
  FileText,
  LogOut,
  Menu,
  PanelLeft,
  Plus,
  UserCheck,
} from "lucide-react";
import { BrandMark } from "./brand";
import { ConversationList } from "./conversation-list";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { useWorkspace } from "@/hooks/workspace";
import { useAuth, useAuthClient } from "@/hooks/auth";
import { ConversationsProvider } from "@/hooks/conversations";
import { AuthError } from "@/services/auth-api";
import { cn } from "@/utils/cn";

type Section = "ask" | "files" | "inquiries" | "admin";

const SECTION_NAMES: Record<Section, string> = {
  ask: "Hỏi đáp",
  files: "Thư viện tài liệu",
  inquiries: "Yêu cầu chuyên gia",
  admin: "Kho kiến thức",
};

const SIDEBAR_ID = "app-sidebar";

export type QaShellOutletContext = {
  setKnowledgeNavigationLocked: (locked: boolean) => void;
};

/**
 * Map the current path to the navigation section it belongs to.
 *
 * Source-detail pages open from the library, so they highlight the library.
 */
function sectionFor(pathname: string): Section {
  if (pathname.startsWith("/admin")) return "admin";
  if (pathname.startsWith("/inquiries")) return "inquiries";
  if (pathname.startsWith("/files") || pathname.startsWith("/knowledge"))
    return "files";
  return "ask";
}

/** Show the signed-in user and the sign-out action with its error feedback. */
function AccountPanel() {
  const auth = useAuth();
  const authClient = useAuthClient();
  const [logoutError, setLogoutError] = useState("");
  if (auth.status !== "signed-in") return null;
  return (
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
        <LogOut size={16} aria-hidden="true" />
        Đăng xuất
      </button>
      {logoutError && (
        <p role="alert" className="inline-error sidebar-error">
          {logoutError}
        </p>
      )}
    </div>
  );
}

/** Link that starts a new chat; active only on the empty chat route. */
function NewChatLink({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <NavLink
      to="/ask"
      end
      className={({ isActive }) => cn("new-chat-button", isActive && "active")}
      data-testid="new-chat"
      onClick={onNavigate}
    >
      <Plus size={17} aria-hidden="true" />
      Trò chuyện mới
    </NavLink>
  );
}

/** Desktop sidebar: brand, sections, recent chats and account controls. */
function Sidebar({ section }: { section: Section }) {
  const auth = useAuth();
  const isAdmin = auth.status === "signed-in" && auth.user.role === "admin";
  const sectionLink = (key: Section) => ({
    className: cn("sidebar-link", section === key && "active"),
    "aria-current": section === key ? ("page" as const) : undefined,
  });
  return (
    <div className="sidebar-content">
      <Link to="/ask" className="sidebar-brand">
        <BrandMark small />
        <span>Factory Memo</span>
      </Link>
      <nav className="sidebar-nav" aria-label="Chức năng">
        <NewChatLink />
        <Link to="/files" data-testid="nav-files" {...sectionLink("files")}>
          <FileText size={17} aria-hidden="true" />
          Thư viện tài liệu
        </Link>
        <Link
          to="/inquiries"
          data-testid="nav-inquiries"
          {...sectionLink("inquiries")}
        >
          <UserCheck size={17} aria-hidden="true" />
          Yêu cầu chuyên gia
        </Link>
        {isAdmin && (
          <Link
            to="/admin/knowledge-bases"
            data-testid="nav-admin-knowledge"
            {...sectionLink("admin")}
          >
            <BookOpen size={17} aria-hidden="true" />
            Kho kiến thức
          </Link>
        )}
      </nav>
      <div className="sidebar-scroll has-list">
        <ConversationList />
      </div>
      <AccountPanel />
    </div>
  );
}

/** Phone section tabs; one row replaces the hidden desktop navigation. */
function MobileSectionNav({ section }: { section: Section }) {
  const auth = useAuth();
  const isAdmin = auth.status === "signed-in" && auth.user.role === "admin";
  const tabs: { to: string; key: Section; label: string }[] = [
    { to: "/ask", key: "ask", label: "Hỏi đáp" },
    { to: "/files", key: "files", label: "Tài liệu" },
    { to: "/inquiries", key: "inquiries", label: "Chuyên gia" },
    ...(isAdmin
      ? [
          {
            to: "/admin/knowledge-bases",
            key: "admin" as const,
            label: "Kho kiến thức",
          },
        ]
      : []),
  ];
  return (
    <nav
      className="app-mobile-nav"
      aria-label="Chức năng"
      style={{ gridTemplateColumns: `repeat(${tabs.length}, minmax(0, 1fr))` }}
    >
      {tabs.map((tab) => (
        <Link
          key={tab.key}
          to={tab.to}
          data-testid={`mobile-nav-${tab.key}`}
          aria-current={section === tab.key ? "page" : undefined}
        >
          {tab.label}
        </Link>
      ))}
    </nav>
  );
}

/**
 * Live mode shell. Desktop shows a collapsible sidebar; phones show section
 * tabs and a drawer with chat history and account actions. Collapse state
 * lives here so the routed page, its draft and any stream stay mounted.
 */
export function QaShell() {
  const workspace = useWorkspace();
  const auth = useAuth();
  const section = sectionFor(useLocation().pathname);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const closeDrawer = () => setDrawerOpen(false);
  const toggleLabel = sidebarOpen ? "Thu gọn thanh bên" : "Mở thanh bên";
  return (
    <ConversationsProvider
      key={auth.status === "signed-in" ? auth.user.id : auth.status}
    >
      <Sheet open={drawerOpen} onOpenChange={setDrawerOpen}>
        <div
          className={cn(
            "app-shell chat-focus-shell",
            !sidebarOpen && "sidebar-closed",
          )}
          data-testid="chat-focus-root"
        >
          <aside
            id={SIDEBAR_ID}
            className="app-sidebar"
            aria-label="Điều hướng"
            hidden={!sidebarOpen}
          >
            <Sidebar section={section} />
          </aside>
          <div className="app-main">
            <header className="app-topbar">
              <button
                type="button"
                className="app-icon-button sidebar-toggle"
                aria-controls={SIDEBAR_ID}
                aria-expanded={sidebarOpen}
                aria-label={toggleLabel}
                title={toggleLabel}
                data-testid="sidebar-toggle"
                onClick={() => setSidebarOpen((open) => !open)}
              >
                <PanelLeft size={18} aria-hidden="true" />
              </button>
              {/* The trigger lets the drawer return focus here when it closes. */}
              <SheetTrigger asChild>
                <button
                  type="button"
                  className="app-icon-button app-menu-button"
                  aria-label="Mở lịch sử trò chuyện"
                  data-testid="open-menu"
                >
                  <Menu size={20} aria-hidden="true" />
                </button>
              </SheetTrigger>
              <Link to="/ask" className="sidebar-brand app-topbar-brand">
                <BrandMark small />
                <span>Factory Memo</span>
              </Link>
              <span className="app-topbar-section">
                {SECTION_NAMES[section]}
              </span>
            </header>
            <MobileSectionNav section={section} />
            {workspace.error && (
              <div className="workspace-error" role="alert">
                {workspace.error}
              </div>
            )}
            <div className="workspace-outlet">
              <Outlet />
            </div>
          </div>
          <SheetContent side="left" className="app-drawer">
            <SheetHeader className="app-drawer-header">
              <SheetTitle>Trò chuyện</SheetTitle>
              <SheetDescription className="sr-only">
                Lịch sử trò chuyện và tài khoản.
              </SheetDescription>
            </SheetHeader>
            <div className="sidebar-content">
              <NewChatLink onNavigate={closeDrawer} />
              <div className="sidebar-scroll has-list">
                <ConversationList onNavigate={closeDrawer} />
              </div>
              <AccountPanel />
            </div>
          </SheetContent>
        </div>
      </Sheet>
    </ConversationsProvider>
  );
}
