import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowUp,
  ImageIcon,
  Loader2,
  Mic,
  RotateCw,
  Square,
} from "lucide-react";
import {
  MAX_THINKING_CHARACTERS,
  useConversations,
} from "@/hooks/conversations";
import { useDictation } from "@/hooks/dictation";
import { BrandMark } from "@/components/brand";
import { CitedAnswer } from "@/components/cited-answer";
import { ChatToolCalls } from "@/components/chat-tool-calls";
import { ThinkingBlock } from "@/components/thinking-block";
import { Button } from "@/components/ui/button";
import {
  parseChatTools,
  parseReplyId,
  reconcileChatTools,
  type ChatTool,
} from "@/services/chat-tools";
import {
  ConversationError,
  parseCitations,
  parseSearchStatus,
  type Citation,
  type SearchStatus,
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
  citations: Citation[];
  tools?: ChatTool[];
  search?: SearchStatus;
  hasPhoto?: boolean;
  /** Streamed model reasoning retained in this tab's memory only. */
  reasoning?: string;
  thinkingSeconds?: number;
};

const TITLE_LENGTH = 80;

const errorMessage = (cause: unknown, fallback: string) =>
  cause instanceof ConversationError ? cause.message : fallback;

/** Live chat with the standalone agent; conversations are saved on the server. */
export function ConversationsPage() {
  const { threadId: conversationId } = useParams();
  const navigate = useNavigate();
  const { api, list, remember, rememberThinking, thinkingFor } =
    useConversations();
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
  const current = list.find((item) => item.id === conversationId);
  // The transcript goes into the composer for editing; it is never sent here.
  const dictation = useDictation(
    (wav, signal) => api.transcribe(wav, signal),
    (text, target) => {
      if (target !== shown.current) return;
      setQuery((value) => (value.trim() ? `${value.trimEnd()} ${text}` : text));
      composer.current?.focus();
    },
  );
  const cancelDictation = dictation.cancel;

  useEffect(() => {
    // Switching conversations stops the old answer; it never updates this one.
    if (shown.current !== conversationId && controller.current) {
      controller.current.abort();
      controller.current = null;
      setSending(false);
    }
    if (shown.current !== conversationId) {
      // The draft and any pending transcript belong to the chat being left.
      createdHere.current = undefined;
      cancelDictation();
      setQuery("");
    }
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
        if (abort.signal.aborted) return;
        setTurns(
          history.messages.map((message, index) => {
            const replyId = message.role === "assistant"
              ? parseReplyId(message.reply_id)
              : undefined;
            return {
              key: replyId ?? `saved-${index}`,
              role: message.role,
              text: message.text,
              status: "saved",
              citations: parseCitations(message.citations),
              tools: parseChatTools(message.tools),
              search: parseSearchStatus(message.search),
              hasPhoto: Boolean(message.attachment),
              ...(replyId ? thinkingFor(conversationId, replyId) : undefined),
            };
          }),
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
  }, [api, cancelDictation, conversationId, historyReload, thinkingFor]);

  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [turns]);

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
        citations: [],
      },
      {
        key: answerKey,
        role: "assistant",
        text: "",
        status: "streaming",
        citations: [],
      },
    ]);
    let text = "";
    let reasoning = "";
    let toolCalls: ChatTool[] = [];
    let thinkingStarted: number | undefined;
    let elapsed: number | undefined;
    // Thinking ends when the first answer text arrives.
    const thinkingSeconds = () =>
      thinkingStarted === undefined
        ? undefined
        : Math.round((Date.now() - thinkingStarted) / 1000);
    try {
      for await (const event of api.stream(id, message, abort.signal)) {
        if (abort.signal.aborted || shown.current !== id) {
          update({ status: "stopped" });
          break;
        }
        if (event.type === "reasoning") {
          thinkingStarted ??= Date.now();
          reasoning = (reasoning + event.delta).slice(0, MAX_THINKING_CHARACTERS);
          update({ reasoning });
        } else if (event.type === "text") {
          if (!text) {
            elapsed = thinkingSeconds();
            update({ thinkingSeconds: elapsed });
          }
          text += event.delta;
          update({ text });
        } else if (event.type === "tool") {
          const tool = event.tool;
          toolCalls = toolCalls.some((item) => item.id === tool.id)
            ? toolCalls.map((item) => (item.id === tool.id ? tool : item))
            : [...toolCalls, tool].slice(0, 2);
          update({ tools: toolCalls });
        } else if (event.type === "complete") {
          elapsed ??= thinkingSeconds();
          if (event.reply_id && reasoning) {
            rememberThinking(id, event.reply_id, {
              reasoning,
              thinkingSeconds: elapsed,
            });
          }
          update({
            text: event.answer,
            status: "saved",
            citations: event.citations,
            search: event.search,
            tools: event.tools
              ? reconcileChatTools(event.tools, toolCalls)
              : toolCalls,
            thinkingSeconds: elapsed,
          });
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

  return (
    <div className="ask-layout agent-chat-layout">
      <main className="chat-area">
        <header className="chat-header">
          <h1 data-testid="chat-title">
            {current?.title ?? (conversationId ? "" : "Trò chuyện mới")}
          </h1>
        </header>
        <div className="chat-scroll" ref={scroll}>
          {historyState === "loading" ? (
            <div className="chat-empty" aria-busy="true">
              <p>Đang tải cuộc trò chuyện…</p>
            </div>
          ) : !turns.length ? (
            <div className="chat-empty">
              <h2>Máy đang gặp vấn đề gì?</h2>
              <p>
                Mô tả sự cố và cung cấp mã lỗi hoặc thông tin về máy nếu có.
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
                    {turn.hasPhoto && (
                      <span
                        className="field-note"
                        data-testid="photo-reference"
                      >
                        <ImageIcon size={13} /> Có ảnh đính kèm
                      </span>
                    )}
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
                    {turn.reasoning && (
                      <ThinkingBlock
                        reasoning={turn.reasoning}
                        thinking={turn.status === "streaming" && !turn.text}
                        seconds={turn.thinkingSeconds}
                      />
                    )}
                    {turn.tools && turn.tools.length > 0 && (
                      <ChatToolCalls
                        calls={turn.tools}
                        citations={turn.citations}
                        streaming={turn.status === "streaming"}
                      />
                    )}
                    <div className="answer-prose" data-testid="answer-text">
                      {turn.text ? (
                        <CitedAnswer
                          text={turn.text}
                          citations={turn.citations}
                          search={turn.search}
                        />
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
          {dictation.error && (
            <p
              role="alert"
              className="inline-error"
              data-testid="dictation-error"
            >
              {dictation.error}
            </p>
          )}
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
              <span>
                {dictation.state === "recording"
                  ? "Đang ghi âm… bấm lại để dừng (tối đa 2 phút)"
                  : dictation.state === "transcribing"
                    ? "Đang nhận dạng giọng nói…"
                    : "Thêm mã lỗi hoặc kiểu máy nếu có"}
              </span>
              <span className="composer-actions">
                <Button
                  type="button"
                  variant={
                    dictation.state === "recording" ? "default" : "ghost"
                  }
                  size="icon"
                  aria-label={
                    dictation.state === "recording"
                      ? "Dừng ghi âm"
                      : "Ghi âm câu hỏi"
                  }
                  data-testid="dictation"
                  disabled={sending || dictation.state === "transcribing"}
                  onClick={() =>
                    void (dictation.state === "recording"
                      ? dictation.stop()
                      : dictation.start(conversationId))
                  }
                >
                  {dictation.state === "transcribing" ? (
                    <Loader2 size={16} className="animate-spin" />
                  ) : dictation.state === "recording" ? (
                    <Square size={14} fill="currentColor" />
                  ) : (
                    <Mic size={16} />
                  )}
                </Button>
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
              </span>
            </div>
          </form>
        </div>
      </main>
    </div>
  );
}
