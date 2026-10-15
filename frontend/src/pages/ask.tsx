import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowDown,
  ArrowUp,
  BookOpen,
  Check,
  Copy,
  Search,
  Square,
} from "lucide-react";
import Markdown from "react-markdown";
import type { ChatMessage, ChatThread, Citation } from "@/types";
import { useWorkspace } from "@/hooks/workspace";
import { createKnowledgeClient } from "@/services/chat";
import { buildCard, currentCard } from "@/services/analysis";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { EvidencePanel } from "@/components/evidence-panel";
import { BrandMark } from "@/components/brand";

const client = createKnowledgeClient();

function AnswerContent({
  message,
  onCitation,
}: {
  message: ChatMessage;
  onCitation(number: number): void;
}) {
  return (
    <Markdown
      components={{
        a: ({ href, children }) =>
          href?.startsWith("#citation-") ? (
            <button
              type="button"
              className="inline-citation"
              data-testid="citation-marker"
              data-citation={href.slice(10)}
              aria-label={`Mở nguồn ${href.slice(10)}`}
              onClick={() => onCitation(Number(href.slice(10)))}
            >
              {children}
            </button>
          ) : (
            <span>{children}</span>
          ),
      }}
    >
      {message.content.replace(/\[(\d+)\]/g, "[$1](#citation-$1)")}
    </Markdown>
  );
}

export function AskPage() {
  const { threadId } = useParams();
  const navigate = useNavigate();
  const workspace = useWorkspace();
  const thread = workspace.threads.find((item) => item.id === threadId);
  const [query, setQuery] = useState("");
  const [sending, setSending] = useState(false);
  const [selectedMessage, setSelectedMessage] = useState("");
  const [activeCitation, setActiveCitation] = useState<Citation>();
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [copied, setCopied] = useState("");
  const [localError, setLocalError] = useState("");
  const [awayFromBottom, setAwayFromBottom] = useState(false);
  const scroll = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const controller = useRef<AbortController | null>(null);
  const activeThread = useRef<string | undefined>(undefined);
  const nearBottom = useRef(true);
  const answers =
    thread?.messages.filter((message) => message.role === "assistant") ?? [];
  const selected =
    answers.find((answer) => answer.id === selectedMessage) ?? answers.at(-1);
  const citations = selected?.citations ?? [];
  const card =
    currentCard(workspace.cards) ??
    buildCard(
      workspace.sources,
      workspace.sources.map((source) => source.id),
    );

  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    controller.current?.abort();
  }, [workspace.generation]);
  useEffect(() => {
    if (controller.current && activeThread.current !== threadId)
      controller.current.abort();
    setSelectedMessage("");
    setActiveCitation(undefined);
    nearBottom.current = true;
  }, [threadId]);
  useEffect(() => {
    if (nearBottom.current && scroll.current)
      scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [thread?.messages]);

  function openCitation(message: ChatMessage, number: number) {
    const citation = message.citations.find((item) => item.number === number);
    if (!citation) return;
    setSelectedMessage(message.id);
    setActiveCitation(citation);
    if (window.matchMedia("(max-width: 1023px)").matches) setSourcesOpen(true);
  }

  async function send(value = query) {
    if (!value.trim() || sending) return;
    setQuery("");
    setSending(true);
    setLocalError("");
    setActiveCitation(undefined);
    const abort = new AbortController();
    controller.current = abort;
    const question = value.trim();
    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: question,
      citations: [],
      status: "complete",
    };
    let answer: ChatMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      content: "",
      citations: [],
      status: "streaming",
    };
    let current: ChatThread = {
      id: thread?.id ?? crypto.randomUUID(),
      title: thread?.title ?? question,
      updatedAt: new Date().toISOString(),
      messages: [...(thread?.messages ?? []), userMessage, answer],
    };
    activeThread.current = current.id;
    setSelectedMessage(answer.id);
    nearBottom.current = true;
    workspace.updateThread(current);
    if (!thread) void navigate(`/ask/${current.id}`, { replace: true });
    const update = () => {
      current = {
        ...current,
        messages: current.messages.map((item) =>
          item.id === answer.id ? answer : item,
        ),
      };
      workspace.updateThread(current);
    };
    try {
      await workspace.saveThread(current);
      for await (const event of client.streamAnswer(
        question,
        workspace.sources,
        abort.signal,
      )) {
        if (event.event === "answer.delta")
          answer = { ...answer, content: answer.content + event.text };
        if (event.event === "answer.reset") answer = { ...answer, content: "" };
        if (event.event === "answer.citations")
          answer = { ...answer, citations: event.citations };
        if (event.event === "run.completed")
          answer = { ...answer, status: "complete" };
        update();
      }
    } catch (cause) {
      const stopped = abort.signal.aborted;
      answer = {
        ...answer,
        status: stopped ? "stopped" : "failed",
        content:
          answer.content ||
          (stopped
            ? "Đã dừng câu trả lời."
            : "Không hoàn tất được câu trả lời. Hãy thử lại."),
      };
      update();
      if (!stopped)
        setLocalError(
          cause instanceof Error
            ? cause.message
            : "Không hoàn tất được câu trả lời.",
        );
    } finally {
      update();
      try {
        await workspace.saveThread(current);
      } catch {
        /* Provider displays persistence failures. */
      }
      if (controller.current === abort) {
        controller.current = null;
        setSending(false);
      }
      composer.current?.focus();
    }
  }

  async function copy(message: ChatMessage) {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(message.id);
      setTimeout(() => setCopied(""), 1500);
    } catch {
      setLocalError("Không sao chép được. Hãy bôi đen và sao chép thủ công.");
    }
  }

  const panel = (
    <EvidencePanel
      citations={citations}
      sources={workspace.sources}
      card={card}
      active={activeCitation}
      onSelect={setActiveCitation}
      onBack={() => setActiveCitation(undefined)}
    />
  );
  return (
    <div className="ask-layout">
      <main className="chat-area">
        <div className="chat-mobile-bar">
          <Button
            variant="outline"
            size="sm"
            className="mobile-evidence-button"
            onClick={() => setSourcesOpen(true)}
          >
            <BookOpen size={15} />
            Nguồn
            {citations.length > 0 && (
              <span className="count-pill">{citations.length}</span>
            )}
          </Button>
        </div>
        <div
          className="chat-scroll"
          ref={scroll}
          onScroll={() => {
            if (!scroll.current) return;
            const el = scroll.current;
            nearBottom.current =
              el.scrollHeight - el.scrollTop - el.clientHeight < 100;
            setAwayFromBottom(!nearBottom.current);
          }}
        >
          {!thread?.messages.length ? (
            <div className="chat-empty">
              <h2>Máy đang gặp vấn đề gì?</h2>
              <p>Hỏi bằng tiếng Việt. Câu trả lời luôn kèm nguồn để kiểm tra.</p>
            </div>
          ) : (
            <div className="message-list">
              {thread.messages.map((message) =>
                message.role === "user" ? (
                  <div
                    key={message.id}
                    className="user-message"
                    data-testid="chat-question"
                  >
                    <p>{message.content}</p>
                  </div>
                ) : (
                  <article
                    key={message.id}
                    className={`assistant-message ${selected?.id === message.id ? "selected-answer" : ""}`}
                    data-testid="chat-answer"
                    data-status={message.status}
                  >
                    <div className="assistant-label">
                      <BrandMark small />
                      <span>Factory memo</span>
                      <span className="scope-pill">Phạm vi: MR-JE-AX167</span>
                    </div>
                    <div className="answer-prose">
                      {message.content ? (
                        <AnswerContent
                          message={message}
                          onCitation={(number) => openCitation(message, number)}
                        />
                      ) : (
                        <div
                          className="thinking-status"
                          role="status"
                          data-testid="chat-searching"
                        >
                          <Search size={15} />
                          Đang tìm nguồn liên quan…
                          <span className="scope-pill">
                            Phạm vi: MR-JE-AX167
                          </span>
                        </div>
                      )}
                    </div>
                    {message.status === "streaming" && message.content && (
                      <span
                        className="stream-cursor"
                        aria-label="Đang tạo câu trả lời"
                      />
                    )}
                    {message.status !== "streaming" && (
                      <div className="answer-actions">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            setSelectedMessage(message.id);
                            setActiveCitation(undefined);
                            if (
                              window.matchMedia("(max-width: 1023px)").matches
                            )
                              setSourcesOpen(true);
                          }}
                        >
                          <BookOpen size={14} />
                          {message.citations.length} nguồn
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label="Sao chép câu trả lời"
                          onClick={() => void copy(message)}
                        >
                          {copied === message.id ? (
                            <Check size={14} />
                          ) : (
                            <Copy size={14} />
                          )}
                        </Button>
                        {message.status === "stopped" && (
                          <span className="field-note">Đã dừng</span>
                        )}
                      </div>
                    )}
                  </article>
                ),
              )}
            </div>
          )}
        </div>
        {awayFromBottom && (
          <Button
            variant="outline"
            size="icon"
            className="scroll-to-bottom"
            aria-label="Cuộn tới câu trả lời mới nhất"
            onClick={() => {
              nearBottom.current = true;
              scroll.current?.scrollTo({
                top: scroll.current.scrollHeight,
                behavior: "smooth",
              });
            }}
          >
            <ArrowDown size={16} />
          </Button>
        )}
        <div className="composer-wrap">
          {localError && (
            <p role="alert" className="inline-error">
              {localError}
            </p>
          )}
          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault();
              void send();
            }}
          >
            <textarea
              ref={composer}
              rows={2}
              aria-label="Đặt câu hỏi"
              data-testid="chat-input"
              placeholder="Hỏi về máy, sự cố hoặc quy trình…"
              value={query}
              maxLength={20000}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <div className="composer-bottom">
              <span>
                <BookOpen size={13} />
                Tri thức đã duyệt + manual
              </span>
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
                  disabled={!query.trim() || workspace.loading}
                >
                  <ArrowUp size={19} />
                </Button>
              )}
            </div>
          </form>
          <p className="composer-note">
            Mỗi câu trả lời dẫn về đúng nguồn gốc.
          </p>
        </div>
      </main>
      <aside className="desktop-evidence">{panel}</aside>
      <Sheet open={sourcesOpen} onOpenChange={setSourcesOpen}>
        <SheetContent side="right" className="evidence-sheet">
          <SheetHeader className="sr-only">
            <SheetTitle>Nguồn của câu trả lời</SheetTitle>
            <SheetDescription>
              Xem tài liệu và bản ghi được câu trả lời trích dẫn.
            </SheetDescription>
          </SheetHeader>
          {panel}
        </SheetContent>
      </Sheet>
    </div>
  );
}
