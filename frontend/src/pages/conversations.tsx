import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowUp, Pencil, RotateCw, Square, Trash2 } from "lucide-react";
import Markdown from "react-markdown";
import { useAuthClient } from "@/hooks/auth";
import { BrandMark } from "@/components/brand";
import { Button } from "@/components/ui/button";
import {
  ConversationError,
  ConversationsApi,
  type Conversation,
} from "@/services/conversations-api";

/**
 * One displayed turn. `streaming` text is provisional until the agent
 * confirms the save; `unconfirmed` means the stream ended without a final
 * event, so the answer may or may not have been saved.
 */
type Turn = {
  key: string;
  role: "user" | "assistant";
  text: string;
  status: "saved" | "streaming" | "failed" | "stopped" | "unconfirmed";
  error?: string;
};

const TITLE_LENGTH = 80;

const errorMessage = (cause: unknown, fallback: string) =>
  cause instanceof ConversationError ? cause.message : fallback;

/** Live chat with the standalone agent; conversations are saved on the server. */
export function ConversationsPage() {
  const { threadId: conversationId } = useParams();
  const navigate = useNavigate();
  const auth = useAuthClient();
  const api = useMemo(() => new ConversationsApi(auth), [auth]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [listError, setListError] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [historyState, setHistoryState] = useState<
    "idle" | "loading" | "error"
  >("idle");
  const [historyReload, setHistoryReload] = useState(0);
  // The chat this page created and is showing without loading its history.
  const createdHere = useRef<string | undefined>(undefined);
  const reload = () => {
    createdHere.current = undefined;
    setHistoryReload((value) => value + 1);
  };
  const [query, setQuery] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  // The conversation whose history and stream the screen currently shows.
  const shown = useRef<string | undefined>(conversationId);
  const scroll = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const current = conversations.find((item) => item.id === conversationId);

  useEffect(() => {
    const abort = new AbortController();
    api
      .list(abort.signal)
      .then((items) => {
        setConversations(items);
        setListError("");
      })
      .catch((cause: unknown) => {
        if (!abort.signal.aborted)
          setListError(
            errorMessage(cause, "Không tải được danh sách trò chuyện."),
          );
      });
    return () => abort.abort();
  }, [api]);

  useEffect(() => {
    // Switching conversations stops the old answer; it never updates this one.
    if (shown.current !== conversationId && controller.current) {
      controller.current.abort();
      controller.current = null;
      setSending(false);
    }
    if (shown.current !== conversationId) createdHere.current = undefined;
    shown.current = conversationId;
    setError("");
    setTruncated(false);
    if (!conversationId) {
      setTurns([]);
      setHistoryState("idle");
      return;
    }
    // A chat created by this page already shows its turns; reload only on request.
    if (conversationId === createdHere.current) return;
    const abort = new AbortController();
    setHistoryState("loading");
    api
      .history(conversationId, abort.signal)
      .then((history) => {
        setTurns(
          history.messages.map((message, index) => ({
            key: `saved-${index}`,
            role: message.role,
            text: message.text,
            status: "saved",
          })),
        );
        setTruncated(history.truncated);
        setHistoryState("idle");
      })
      .catch((cause: unknown) => {
        if (abort.signal.aborted) return;
        setTurns([]);
        setHistoryState("error");
        setError(errorMessage(cause, "Không tải được cuộc trò chuyện."));
      });
    return () => abort.abort();
  }, [api, conversationId, historyReload]);

  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [turns]);

  function remember(conversation: Conversation) {
    setConversations((items) => [
      conversation,
      ...items.filter((item) => item.id !== conversation.id),
    ]);
  }

  async function send() {
    const message = query.trim();
    if (!message || sending || historyState !== "idle") return;
    setSending(true);
    setError("");
    const abort = new AbortController();
    controller.current = abort;
    const release = () => {
      if (controller.current !== abort) return;
      controller.current = null;
      setSending(false);
    };
    let id = conversationId;
    try {
      if (!id) {
        // Titles allow no line breaks or other control characters.
        const title = message
          .replace(/\s+/g, " ")
          .slice(0, TITLE_LENGTH)
          .trim();
        const created = await api.create(title, abort.signal);
        remember(created);
        // Stopped, or the user opened another chat while it was being created.
        if (abort.signal.aborted) {
          release();
          return;
        }
        id = created.id;
        shown.current = id;
        createdHere.current = id;
        void navigate(`/ask/${id}`);
      } else if (current) {
        remember({ ...current, updated_at: new Date().toISOString() });
      }
    } catch (cause) {
      if (!abort.signal.aborted)
        setError(errorMessage(cause, "Không tạo được cuộc trò chuyện."));
      release();
      return;
    }
    setQuery("");
    const answerKey = crypto.randomUUID();
    const update = (change: Partial<Turn>) => {
      if (shown.current !== id) return;
      setTurns((items) =>
        items.map((item) =>
          item.key === answerKey ? { ...item, ...change } : item,
        ),
      );
    };
    setTurns((items) => [
      ...items,
      {
        key: crypto.randomUUID(),
        role: "user",
        text: message,
        status: "saved",
      },
      { key: answerKey, role: "assistant", text: "", status: "streaming" },
    ]);
    let text = "";
    try {
      for await (const event of api.stream(id, message, abort.signal)) {
        if (event.type === "text") {
          text += event.delta;
          update({ text });
        } else if (event.type === "complete") {
          update({ text: event.answer, status: "saved" });
        } else if (event.type === "error") {
          update({ status: "failed", error: event.message });
        } else if (event.type === "interrupted") {
          update({ status: "unconfirmed" });
        }
      }
    } catch (cause) {
      if (abort.signal.aborted) update({ status: "stopped" });
      else
        update({
          status: "failed",
          error: errorMessage(cause, "Không hoàn tất được câu trả lời."),
        });
    } finally {
      release();
      composer.current?.focus();
    }
  }

  async function rename() {
    if (!current) return;
    const title = window.prompt("Tên cuộc trò chuyện", current.title)?.trim();
    if (!title || title === current.title) return;
    try {
      remember(await api.rename(current.id, title));
    } catch (cause) {
      setError(errorMessage(cause, "Không đổi tên được."));
    }
  }

  async function remove() {
    if (!current || !window.confirm(`Xoá cuộc trò chuyện "${current.title}"?`))
      return;
    try {
      await api.remove(current.id);
      setConversations((items) =>
        items.filter((item) => item.id !== current.id),
      );
      void navigate("/ask");
    } catch (cause) {
      setError(errorMessage(cause, "Không xoá được cuộc trò chuyện."));
    }
  }

  return (
    <div className="ask-layout agent-chat-layout">
      <main className="chat-area">
        <div className="qa-toolbar">
          <label>
            Cuộc trò chuyện
            <select
              aria-label="Cuộc trò chuyện"
              data-testid="conversation-select"
              value={conversationId ?? ""}
              disabled={sending}
              onChange={(event) =>
                void navigate(
                  event.target.value ? `/ask/${event.target.value}` : "/ask",
                )
              }
            >
              <option value="">Cuộc trò chuyện mới</option>
              {conversationId && !current && (
                <option value={conversationId}>Đang tải…</option>
              )}
              {conversations.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title}
                </option>
              ))}
            </select>
          </label>
          <Button
            variant="outline"
            size="sm"
            disabled={sending || !conversationId}
            onClick={() => void navigate("/ask")}
          >
            Trò chuyện mới
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Đổi tên cuộc trò chuyện"
            disabled={sending || !current}
            onClick={() => void rename()}
          >
            <Pencil size={15} />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Xoá cuộc trò chuyện"
            data-testid="conversation-delete"
            disabled={sending || !current}
            onClick={() => void remove()}
          >
            <Trash2 size={15} />
          </Button>
          {listError && (
            <p role="alert" className="inline-error">
              {listError}
            </p>
          )}
        </div>
        <div className="chat-scroll" ref={scroll}>
          {historyState === "loading" ? (
            <div className="chat-empty" aria-busy="true">
              <p>Đang tải cuộc trò chuyện…</p>
            </div>
          ) : !turns.length ? (
            <div className="chat-empty">
              <h2>Máy đang gặp vấn đề gì?</h2>
              <p>
                Mô tả sự cố bằng tiếng Việt. Trợ lý chưa tra cứu kho tri thức,
                hãy kiểm tra lại trước khi làm theo.
              </p>
            </div>
          ) : (
            <div className="message-list">
              {truncated && (
                <p className="field-note" data-testid="history-truncated">
                  Chỉ hiển thị các tin nhắn gần nhất.
                </p>
              )}
              {turns.map((turn) =>
                turn.role === "user" ? (
                  <div
                    key={turn.key}
                    className="user-message"
                    data-testid="chat-question"
                  >
                    <p>{turn.text}</p>
                  </div>
                ) : (
                  <article
                    key={turn.key}
                    className="assistant-message"
                    data-testid="chat-answer"
                    data-status={turn.status}
                  >
                    <div className="assistant-label">
                      <BrandMark small />
                      <span>Factory memo</span>
                    </div>
                    <div className="answer-prose">
                      {turn.text ? (
                        <Markdown>{turn.text}</Markdown>
                      ) : turn.status === "streaming" ? (
                        <p role="status" className="field-note">
                          Đang tạo câu trả lời…
                        </p>
                      ) : null}
                    </div>
                    {turn.status === "streaming" && turn.text && (
                      <span className="stream-cursor" />
                    )}
                    {turn.status === "failed" && (
                      <p role="alert" className="inline-error">
                        Trả lời chưa hoàn tất
                        {turn.error ? `: ${turn.error}` : "."} Bạn có thể gửi
                        lại câu hỏi.
                      </p>
                    )}
                    {turn.status === "stopped" && (
                      <p className="field-note">
                        Đã dừng câu trả lời. Trợ lý có thể đã lưu câu trả lời.{" "}
                        <button
                          type="button"
                          className="link-button"
                          onClick={reload}
                        >
                          Tải lại cuộc trò chuyện
                        </button>
                      </p>
                    )}
                    {turn.status === "unconfirmed" && (
                      <p role="alert" className="inline-error">
                        Kết nối bị ngắt trước khi xác nhận đã lưu câu trả lời.{" "}
                        <button
                          type="button"
                          className="link-button"
                          onClick={reload}
                        >
                          Tải lại cuộc trò chuyện
                        </button>
                      </p>
                    )}
                  </article>
                ),
              )}
            </div>
          )}
        </div>
        <div className="composer-wrap">
          {error && (
            <p role="alert" className="inline-error">
              {error}{" "}
              {historyState === "error" && (
                <button type="button" className="link-button" onClick={reload}>
                  <RotateCw size={13} /> Thử lại
                </button>
              )}
            </p>
          )}
          <form
            className="composer"
            onSubmit={(event) => {
              event.preventDefault();
              void send();
            }}
          >
            <textarea
              ref={composer}
              rows={2}
              aria-label="Đặt câu hỏi"
              data-testid="chat-input"
              placeholder="Mô tả máy, mã lỗi hoặc hiện tượng…"
              value={query}
              maxLength={20000}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (
                  event.key === "Enter" &&
                  !event.shiftKey &&
                  !event.nativeEvent.isComposing
                ) {
                  event.preventDefault();
                  void send();
                }
              }}
            />
            <div className="composer-bottom">
              <span>Trợ lý chưa tra cứu kho tri thức</span>
              {sending ? (
                <Button
                  type="button"
                  size="icon"
                  aria-label="Dừng câu trả lời"
                  data-testid="chat-stop"
                  onClick={() => controller.current?.abort()}
                >
                  <Square size={15} fill="currentColor" />
                </Button>
              ) : (
                <Button
                  type="submit"
                  size="icon"
                  aria-label="Gửi câu hỏi"
                  data-testid="chat-send"
                  disabled={!query.trim() || historyState !== "idle"}
                >
                  <ArrowUp size={19} />
                </Button>
              )}
            </div>
          </form>
          <p className="composer-note">
            Câu trả lời là gợi ý, không phải quy trình đã duyệt.
          </p>
        </div>
      </main>
    </div>
  );
}
