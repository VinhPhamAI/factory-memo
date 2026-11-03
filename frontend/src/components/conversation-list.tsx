import { useState } from "react";
import { NavLink, useMatch, useNavigate } from "react-router-dom";
import { Pencil, SquarePen, Trash2 } from "lucide-react";
import { useConversations } from "@/hooks/conversations";
import { ConversationError } from "@/services/conversations-api";
import { cn } from "@/utils/cn";

/**
 * Sidebar list of the user's conversations with a new-chat button.
 * Rename and delete act on one item; deleting the open chat returns to /ask.
 */
export function ConversationList({ onNavigate }: { onNavigate?: () => void }) {
  const { list, loading, error, rename, remove } = useConversations();
  // The sidebar sits outside the chat route, so read the open chat from the URL.
  const threadId = useMatch("/ask/:threadId")?.params.threadId;
  const navigate = useNavigate();
  const [actionError, setActionError] = useState("");

  async function renameItem(id: string, current: string) {
    const title = window.prompt("Tên cuộc trò chuyện", current)?.trim();
    if (!title || title === current) return;
    try {
      setActionError("");
      await rename(id, title);
    } catch (cause) {
      setActionError(
        cause instanceof ConversationError
          ? cause.message
          : "Không đổi tên được.",
      );
    }
  }

  async function removeItem(id: string, title: string) {
    if (!window.confirm(`Xoá cuộc trò chuyện "${title}"?`)) return;
    try {
      setActionError("");
      await remove(id);
      if (id === threadId) void navigate("/ask");
    } catch (cause) {
      setActionError(
        cause instanceof ConversationError
          ? cause.message
          : "Không xoá được cuộc trò chuyện.",
      );
    }
  }

  return (
    <div className="conversation-list">
      <NavLink
        to="/ask"
        end
        className="new-chat-button"
        data-testid="new-chat"
        onClick={onNavigate}
      >
        <SquarePen size={16} />
        Trò chuyện mới
      </NavLink>
      <p className="sidebar-section-label">Cuộc trò chuyện</p>
      {(error || actionError) && (
        <p role="alert" className="inline-error sidebar-error">
          {actionError || error}
        </p>
      )}
      {loading ? (
        <p className="sidebar-empty">Đang tải…</p>
      ) : !list.length && !error ? (
        <p className="sidebar-empty">Chưa có cuộc trò chuyện nào.</p>
      ) : (
        <ul aria-label="Cuộc trò chuyện" data-testid="conversation-list">
          {list.map((item) => (
            <li
              key={item.id}
              className={cn(
                "conversation-item",
                item.id === threadId && "active",
              )}
            >
              <NavLink
                to={`/ask/${item.id}`}
                title={item.title}
                onClick={onNavigate}
                aria-current={item.id === threadId ? "page" : undefined}
              >
                {item.title}
              </NavLink>
              <span className="conversation-actions">
                <button
                  type="button"
                  aria-label={`Đổi tên ${item.title}`}
                  onClick={() => void renameItem(item.id, item.title)}
                >
                  <Pencil size={13} />
                </button>
                <button
                  type="button"
                  aria-label={`Xoá ${item.title}`}
                  data-testid="conversation-delete"
                  onClick={() => void removeItem(item.id, item.title)}
                >
                  <Trash2 size={13} />
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
