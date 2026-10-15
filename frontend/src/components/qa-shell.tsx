import { Link, Outlet } from "react-router-dom";
import { BrandMark } from "./brand";
import { useWorkspace } from "@/hooks/workspace";

/** Live QA has no capture, upload or approval controls. */
export function QaShell() {
  const workspace = useWorkspace();
  return (
    <div className="chat-focus-shell" data-testid="chat-focus-root">
      <header className="chat-focus-topbar">
        <Link to="/ask" className="chat-focus-brand">
          <BrandMark small />
          <span>Factory memo</span>
        </Link>
        <span className="qa-connection">QA agent</span>
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
