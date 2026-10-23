import { Link, NavLink, Outlet } from "react-router-dom";
import { FolderPlus, MessagesSquare } from "lucide-react";
import { BrandMark } from "./brand";
import { useWorkspace } from "@/hooks/workspace";
import { cn } from "@/utils/cn";

/** Live mode: QA chat, voice capture, document uploads and review. */
export function QaShell() {
  const workspace = useWorkspace();
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
        </nav>
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
  );
}
