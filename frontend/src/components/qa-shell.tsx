import { useState } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { FolderPlus, LibraryBig, LogOut, MessagesSquare } from "lucide-react";
import { BrandMark } from "./brand";
import { useWorkspace } from "@/hooks/workspace";
import { useAuth, useAuthClient } from "@/hooks/auth";
import { AuthError } from "@/services/auth-api";
import { cn } from "@/utils/cn";

/** Live mode: QA chat, voice capture, document uploads and review. */
export function QaShell() {
  const workspace = useWorkspace();
  const auth = useAuth();
  const authClient = useAuthClient();
  const [logoutError, setLogoutError] = useState("");
  return (
    <div className="chat-focus-shell" data-testid="chat-focus-root">
      <header className="chat-focus-topbar">
        <Link to="/ask" className="chat-focus-brand">
          <BrandMark small />
          <span>Factory memo</span>
        </Link>
        <nav className="qa-nav" aria-label="Chức năng">
          <NavLink
            to="/ask"
            className={({ isActive }) =>
              cn("qa-nav-link", isActive && "active")
            }
          >
            <MessagesSquare size={16} />
            Hỏi đáp
          </NavLink>
          <NavLink
            to="/capture"
            className={({ isActive }) =>
              cn("qa-nav-link", isActive && "active")
            }
            data-testid="nav-capture"
          >
            <FolderPlus size={16} />
            Thêm dữ liệu
          </NavLink>
          <NavLink
            to="/knowledge"
            className={({ isActive }) =>
              cn("qa-nav-link", isActive && "active")
            }
            data-testid="nav-knowledge-management"
          >
            <LibraryBig size={16} />
            Quản lý tri thức
          </NavLink>
        </nav>
        {auth.status === "signed-in" && (
          <button
            type="button"
            className="qa-nav-link"
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
            title={auth.user.username}
            data-testid="sign-out"
          >
            <LogOut size={16} />
            {auth.user.display_name} · Đăng xuất
          </button>
        )}
      </header>
      {logoutError && (
        <div className="workspace-error" role="alert">
          {logoutError}
        </div>
      )}
      {workspace.error && (
        <div className="workspace-error" role="alert">
          {workspace.error}
        </div>
      )}
      <div className="workspace-outlet">
        <Outlet />
      </div>
    </div>
  );
}
