import { useState } from "react";
import {
  Link,
  NavLink,
  Outlet,
  useLocation,
  useNavigate,
} from "react-router-dom";
import {
  BadgeCheck,
  Check,
  ChevronDown,
  CircleHelp,
  FileText,
  Menu,
  MessageSquare,
  MessageSquarePlus,
  Mic,
  RotateCcw,
  Sparkles,
  X,
  type LucideIcon,
} from "lucide-react";
import { BrandMark } from "./brand";
import { useWorkspace } from "@/hooks/workspace";
import { currentCard } from "@/services/analysis";
import { SOURCE_IDS } from "@/fixtures/ax167";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface Step {
  to: string;
  key: string;
  label: string;
  hint: string;
  icon: LucideIcon;
}
const steps: Step[] = [
  {
    to: "/record",
    key: "record",
    label: "Ghi âm",
    hint: "Giọng nói → transcript",
    icon: Mic,
  },
  {
    to: "/sources",
    key: "sources",
    label: "Tài liệu",
    hint: "Tải lên · AI OCR",
    icon: FileText,
  },
  {
    to: "/analyze",
    key: "analyze",
    label: "Phân tích AI",
    hint: "7 câu hỏi → card",
    icon: Sparkles,
  },
  {
    to: "/card",
    key: "card",
    label: "Knowledge card",
    hint: "Chuyên gia duyệt",
    icon: BadgeCheck,
  },
  {
    to: "/ask",
    key: "ask",
    label: "Hỏi đáp",
    hint: "Trả lời kèm nguồn",
    icon: MessageSquare,
  },
];

export function Shell() {
  const workspace = useWorkspace();
  const location = useLocation();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [resetError, setResetError] = useState("");
  const index = steps.findIndex((step) =>
    location.pathname.startsWith(step.to),
  );
  const card = currentCard(workspace.cards);
  const done: Record<string, boolean> = {
    record: workspace.sources.some(
      (s) => s.id === SOURCE_IDS.conversation && s.origin === "recording",
    ),
    sources: workspace.sources.some((s) => s.ocr),
    analyze: !!card,
    card: card?.status === "approved",
    ask: workspace.threads.length > 0,
  };
  async function reset() {
    setResetting(true);
    setResetError("");
    try {
      await workspace.reset();
      setResetOpen(false);
      void navigate("/record");
    } catch {
      setResetError("Không đặt lại được bộ nhớ trình duyệt. Hãy thử lại.");
    } finally {
      setResetting(false);
    }
  }
  const sidebar = (
    <div className="sidebar-content">
      <Link to="/record" className="brand" onClick={() => setMenuOpen(false)}>
        <BrandMark />
        <span>
          Factory memo
          <span className="brand-caption">Tri thức từ chuyên gia</span>
        </span>
      </Link>
      <span className="sidebar-label">QUY TRÌNH 5 BƯỚC</span>
      <nav className="primary-nav" aria-label="Điều hướng chính">
        {steps.map((step, i) => (
          <NavLink
            key={step.key}
            to={step.to}
            data-testid={`nav-${step.key}`}
            className={({ isActive }) =>
              `nav-item ${isActive ? "active" : ""} ${done[step.key] ? "done" : ""}`
            }
            onClick={() => setMenuOpen(false)}
          >
            <span className="nav-step">
              {done[step.key] ? <Check size={14} strokeWidth={3} /> : i + 1}
            </span>
            <span className="nav-text">
              <strong>{step.label}</strong>
              <span>{step.hint}</span>
            </span>
            <step.icon size={17} className="nav-icon" />
          </NavLink>
        ))}
      </nav>
      <div className="sidebar-history">
        <div className="sidebar-history-head">
          <span className="sidebar-label">HỎI ĐÁP GẦN ĐÂY</span>
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                className="history-new"
                aria-label="Cuộc hỏi đáp mới"
                onClick={() => {
                  void navigate("/ask");
                  setMenuOpen(false);
                }}
              >
                <MessageSquarePlus size={15} />
              </button>
            </TooltipTrigger>
            <TooltipContent side="right">Cuộc hỏi đáp mới</TooltipContent>
          </Tooltip>
        </div>
        {!workspace.threads.length ? (
          <p className="history-empty">Câu hỏi của bạn sẽ hiện ở đây.</p>
        ) : (
          [...workspace.threads]
            .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
            .map((thread) => (
              <Link
                key={thread.id}
                className={`history-item ${location.pathname.endsWith(thread.id) ? "active" : ""}`}
                to={`/ask/${thread.id}`}
                title={thread.title}
                onClick={() => setMenuOpen(false)}
              >
                <MessageSquare size={14} />
                <span>{thread.title}</span>
              </Link>
            ))
        )}
      </div>
      <div className="workspace-footer">
        <span className="workspace-avatar">L11</span>
        <div>
          <strong>Nhà máy · Line 11</strong>
          <span>Lưu trong trình duyệt này</span>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Tùy chọn workspace"
              data-testid="workspace-options"
            >
              <ChevronDown size={15} />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent side="top" align="end">
            <DropdownMenuItem
              disabled={location.pathname === "/record"}
              data-testid="reset-demo"
              onSelect={() => {
                setResetOpen(true);
                setResetError("");
              }}
            >
              <RotateCcw size={14} />
              Đặt lại demo
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
  if (location.pathname.startsWith("/ask"))
    return (
      <div className="chat-focus-shell" data-testid="chat-focus-root">
        <header className="chat-focus-topbar">
          <Link to="/record" className="chat-focus-brand">
            <BrandMark small />
            <span>Factory memo</span>
          </Link>
        </header>
        {workspace.error && (
          <div className="workspace-error" role="alert">
            <span>{workspace.error}</span>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Đóng thông báo lỗi"
              onClick={workspace.clearError}
            >
              <X size={15} />
            </Button>
          </div>
        )}
        <div className="workspace-outlet">
          <Outlet />
        </div>
      </div>
    );
  return (
    <div className="workspace-shell">
      <aside className="desktop-sidebar">{sidebar}</aside>
      <div className="workspace-main">
        <div className="workspace-topbar">
          <div className="topbar-left">
            <Button
              variant="ghost"
              size="icon"
              className="mobile-menu"
              aria-label="Mở điều hướng"
              onClick={() => setMenuOpen(true)}
            >
              <Menu size={19} />
            </Button>
            <span className="workspace-breadcrumb">
              Factory memo<span>/</span>
              <strong>
                {index >= 0
                  ? `Bước ${index + 1} · ${steps[index].label}`
                  : "Workspace"}
              </strong>
            </span>
          </div>
          <div className="topbar-right">
            <span className="topbar-machine">MR-JE-AX167 · Line 11</span>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  className="demo-indicator"
                  aria-label="Về chế độ mô phỏng"
                >
                  <Badge variant="outline">
                    <span className="small-dot" />
                    Mô phỏng
                  </Badge>
                  <CircleHelp size={14} />
                </button>
              </TooltipTrigger>
              <TooltipContent side="bottom" className="max-w-72">
                Ghi âm, OCR, phân tích AI và câu trả lời đều được mô phỏng trên
                trình duyệt bằng dữ liệu thật của case MR-JE-AX167. Không gọi
                backend.
              </TooltipContent>
            </Tooltip>
          </div>
        </div>
        {workspace.error && (
          <div className="workspace-error" role="alert">
            <span>{workspace.error}</span>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Đóng thông báo lỗi"
              onClick={workspace.clearError}
            >
              <X size={15} />
            </Button>
          </div>
        )}
        <div className="workspace-outlet">
          <Outlet />
        </div>
      </div>
      <Sheet open={menuOpen} onOpenChange={setMenuOpen}>
        <SheetContent side="left" className="navigation-sheet">
          <SheetHeader className="sr-only">
            <SheetTitle>Điều hướng</SheetTitle>
            <SheetDescription>Mở một bước của quy trình.</SheetDescription>
          </SheetHeader>
          {sidebar}
        </SheetContent>
      </Sheet>
      <Dialog
        open={resetOpen}
        onOpenChange={(open) => {
          if (!resetting) setResetOpen(open);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Đặt lại workspace demo?</DialogTitle>
            <DialogDescription>
              Tệp đã tải lên, bản ghi âm, knowledge card và các cuộc hỏi đáp
              trong trình duyệt này sẽ bị xóa. Dữ liệu mẫu ban đầu được khôi
              phục.
            </DialogDescription>
          </DialogHeader>
          {resetError && (
            <p role="alert" className="inline-error">
              {resetError}
            </p>
          )}
          <div className="dialog-actions">
            <Button
              variant="outline"
              disabled={resetting}
              onClick={() => setResetOpen(false)}
            >
              Hủy
            </Button>
            <Button
              disabled={resetting}
              data-testid="reset-confirm"
              onClick={() => void reset()}
            >
              {resetting ? "Đang đặt lại…" : "Đặt lại demo"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
