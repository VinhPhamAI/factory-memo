import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowRight,
  BadgeCheck,
  Check,
  CircleCheck,
  LoaderCircle,
  Search,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import type { CardRow, Source } from "@/types";
import { useWorkspace } from "@/hooks/workspace";
import {
  ANALYSIS_TIMING,
  buildCard,
  rowsFor,
  verifyCaseEvidence,
} from "@/services/analysis";
import { sleep } from "@/services/timing";
import { CASE_SUBJECT, scopeChips, SOURCE_IDS } from "@/fixtures/ax167";
import { Button } from "@/components/ui/button";
import { RefChip } from "@/components/ref-chip";
import { SampleBadge, SourceIcon } from "@/components/source-preview";
import { cn } from "@/utils/cn";

type Phase = "idle" | "running" | "verifying" | "done";
type Step = "search" | "quotes" | "answer" | "done";

const relevant = (source: Source) =>
  source.machines.some((m) => m.family === "MR-JE");

export function AnalyzePage() {
  const workspace = useWorkspace();
  const navigate = useNavigate();
  const inputs = workspace.sources
    .filter(relevant)
    // The recording leads; documents follow in the order they were added.
    .sort(
      (a, b) =>
        Number(b.id === SOURCE_IDS.conversation) -
          Number(a.id === SOURCE_IDS.conversation) ||
        a.createdAt.localeCompare(b.createdAt),
    );
  const [excluded, setExcluded] = useState<string[]>([]);
  const selected = inputs
    .map((source) => source.id)
    .filter((id) => !excluded.includes(id));
  const [phase, setPhase] = useState<Phase>("idle");
  const [question, setQuestion] = useState(0);
  const [step, setStep] = useState<Step>("search");
  const [quotes, setQuotes] = useState(0);
  const [words, setWords] = useState(0);
  const [checked, setChecked] = useState(0);
  const [rows, setRows] = useState<CardRow[]>([]);
  const abort = useRef<AbortController | null>(null);
  const activeRef = useRef<HTMLDivElement>(null);
  const verification = verifyCaseEvidence(workspace.sources);
  const hasRecording = selected.includes(SOURCE_IDS.conversation);

  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [question, step]);

  async function start() {
    const controller = new AbortController();
    abort.current = controller;
    const signal = controller.signal;
    const planned = rowsFor(selected);
    const ids = [...selected];
    setRows(planned);
    setPhase("running");
    setChecked(0);
    try {
      await sleep(ANALYSIS_TIMING.intro, signal);
      for (let q = 0; q < planned.length; q++) {
        setQuestion(q);
        setStep("search");
        setQuotes(0);
        setWords(0);
        await sleep(ANALYSIS_TIMING.search, signal);
        setStep("quotes");
        for (let i = 1; i <= planned[q].refs.length; i++) {
          setQuotes(i);
          await sleep(ANALYSIS_TIMING.quote, signal);
        }
        setStep("answer");
        const count = planned[q].text.split(" ").length;
        for (let w = 1; w <= count; w++) {
          setWords(w);
          await sleep(ANALYSIS_TIMING.word, signal);
        }
        setStep("done");
        await sleep(ANALYSIS_TIMING.settle, signal);
      }
      setQuestion(planned.length);
      setPhase("verifying");
      const result = verifyCaseEvidence(workspace.sources);
      for (let i = 1; i <= result.total; i++) {
        await sleep(ANALYSIS_TIMING.verify, signal);
        setChecked(i);
      }
      await sleep(ANALYSIS_TIMING.assemble, signal);
      await workspace.saveCard(buildCard(workspace.sources, ids));
      setPhase("done");
    } catch {
      /* Stopped by leaving the page, or storage failure shown by provider. */
    }
  }

  const stateOf = (index: number) =>
    phase === "idle" || index > question
      ? "pending"
      : index < question
        ? "done"
        : step;
  const titleOf = (id: string) =>
    workspace.sources.find((source) => source.id === id);

  return (
    <main
      className="analyze-page"
      data-testid="analyze-page"
      data-phase={phase}
    >
      <header className="analyze-header">
        <div>
          <span className="eyebrow">BƯỚC 3 · XỬ LÝ</span>
          <h1 className="page-heading">Phân tích AI: rút trích tri thức thành knowledge card</h1>
          <p>
            AI đọc bản ghi âm và tài liệu, trả lời 7 câu hỏi cố định, đối chiếu
            từng trích dẫn với nguồn gốc rồi gắn phạm vi máy.
          </p>
        </div>
        {phase === "idle" ? (
          <Button
            size="lg"
            data-testid="analyze-start"
            disabled={!hasRecording || workspace.loading}
            onClick={() => void start()}
          >
            <Sparkles size={18} />
            Bắt đầu phân tích
          </Button>
        ) : phase === "done" ? (
          <Button
            size="lg"
            className="pulse-cta"
            data-testid="analyze-submit-review"
            onClick={() => void navigate("/card")}
          >
            Gửi chuyên gia duyệt
            <ArrowRight size={18} />
          </Button>
        ) : (
          <span className="analyze-running" data-testid="analyze-running">
            <LoaderCircle size={17} className="animate-spin" />
            {phase === "verifying"
              ? "Đang đối chiếu trích dẫn…"
              : `Câu hỏi ${Math.min(question + 1, 7)}/7`}
          </span>
        )}
      </header>
      <div className="analyze-grid">
        <section className="analyze-inputs">
          <h2>Đầu vào ({selected.length})</h2>
          {inputs.map((source) => {
            const included = selected.includes(source.id);
            const state =
              phase === "idle"
                ? included
                  ? "selected"
                  : "excluded"
                : !included
                  ? "excluded"
                  : phase === "done"
                    ? "done"
                    : "analyzing";
            return (
              <label
                key={source.id}
                className={cn("input-card", state)}
                data-testid={`analysis-input-${source.id}`}
                data-state={state}
              >
                <input
                  type="checkbox"
                  checked={included}
                  disabled={phase !== "idle"}
                  onChange={(e) =>
                    setExcluded((current) =>
                      e.target.checked
                        ? current.filter((id) => id !== source.id)
                        : [...current, source.id],
                    )
                  }
                />
                <span className="input-card-icon">
                  <SourceIcon kind={source.kind} size={16} />
                </span>
                <span className="input-card-text">
                  <strong>
                    {source.title}
                    {source.sample && <SampleBadge />}
                  </strong>
                  <span>
                    {state === "analyzing" ? (
                      <>
                        <LoaderCircle size={12} className="animate-spin" />
                        đang phân tích
                      </>
                    ) : state === "done" ? (
                      <>
                        <Check size={12} />
                        đã đọc
                      </>
                    ) : source.kind === "conversation" ? (
                      `${source.turns?.length ?? 0} lượt nói`
                    ) : (
                      (source.docType ?? "Tài liệu")
                    )}
                  </span>
                </span>
              </label>
            );
          })}
          {!hasRecording && (
            <p className="field-note">
              Cần chọn bản ghi âm trao đổi để bắt đầu phân tích.
            </p>
          )}
        </section>

        <section className="analyze-questions">
          {(rows.length ? rows : rowsFor(selected)).map((row, index) => {
            const state = stateOf(index);
            const visibleRefs =
              state === "done" || state === "answer"
                ? row.refs
                : state === "quotes"
                  ? row.refs.slice(0, quotes)
                  : [];
            const answer =
              state === "done"
                ? row.text
                : state === "answer"
                  ? row.text.split(" ").slice(0, words).join(" ")
                  : "";
            const current = phase !== "idle" && index === question;
            return (
              <div
                key={row.key}
                ref={current ? activeRef : undefined}
                className={cn("question-block", state, current && "current")}
                data-testid={`analysis-question-${index + 1}`}
                data-state={state}
              >
                <div className="question-head">
                  <span className="question-number">
                    {state === "done" ? (
                      <Check size={14} strokeWidth={3} />
                    ) : (
                      index + 1
                    )}
                  </span>
                  <strong>{row.question}</strong>
                  <span className="question-target">→ {row.label}</span>
                </div>
                {state === "search" && (
                  <p className="question-searching">
                    <Search size={14} />
                    đang tìm trong ngữ cảnh…
                  </p>
                )}
                {visibleRefs.length > 0 && (
                  <ul
                    className="evidence-quotes"
                    data-testid="analysis-evidence"
                  >
                    {visibleRefs.map((ref) => (
                      <li key={ref.label + ref.quote}>
                        <RefChip evidence={ref} />
                        <q>{ref.quote}</q>
                        {titleOf(ref.sourceId)?.sample && <SampleBadge />}
                      </li>
                    ))}
                  </ul>
                )}
                {answer && (
                  <p
                    className="question-answer"
                    data-testid={`analysis-answer-${index + 1}`}
                  >
                    {answer}
                    {state === "answer" && <span className="stream-cursor" />}
                  </p>
                )}
              </div>
            );
          })}
        </section>

        <section className="analyze-card">
          <div className="mini-card">
            <div className="mini-card-head">
              <BadgeCheck size={18} />
              <div>
                <strong>{CASE_SUBJECT} · Case</strong>
                <span>Knowledge card · đang lắp ráp</span>
              </div>
            </div>
            {phase === "idle" && (
              <p className="mini-card-empty">
                Các dòng của card sẽ hiện ở đây khi AI trả lời xong từng câu
                hỏi.
              </p>
            )}
            {rows.map((row, index) =>
              phase !== "idle" &&
              (index < question || stateOf(index) === "done") ? (
                <div
                  key={row.key}
                  className="mini-row fly-in"
                  data-testid={`card-preview-row-${row.key}`}
                >
                  <span>{row.label}</span>
                  <p>{row.text}</p>
                </div>
              ) : null,
            )}
          </div>
          {(phase === "verifying" || phase === "done") && (
            <div
              className={cn(
                "verify-box",
                verification.matched < verification.total && "warn",
              )}
              data-testid="analysis-verify"
              data-matched={verification.matched}
            >
              <strong>
                {verification.matched === verification.total ? (
                  <CircleCheck size={17} />
                ) : (
                  <TriangleAlert size={17} />
                )}
                Đối chiếu trích dẫn: {verification.matched}/{verification.total}{" "}
                khớp bản ghi gốc
              </strong>
              <div className="verify-checks">
                {verification.results.map((item, i) => (
                  <span
                    key={item.turnCode}
                    className={cn(
                      "verify-check",
                      i < checked && (item.ok ? "ok" : "fail"),
                    )}
                  >
                    {i < checked && item.ok && (
                      <Check size={12} strokeWidth={3} />
                    )}
                    {item.turnCode}
                  </span>
                ))}
              </div>
            </div>
          )}
          {phase === "done" && (
            <div className="scope-box" data-testid="analysis-scope">
              <span>Phạm vi</span>
              <div>
                {scopeChips.map((chip) => (
                  <span key={chip} className="scope-tag">
                    {chip}
                  </span>
                ))}
              </div>
            </div>
          )}
          {phase === "done" && (
            <p className="analysis-done" data-testid="analysis-done">
              <CircleCheck size={16} />
              Đã tạo knowledge card · trạng thái Nháp, chờ chuyên gia duyệt
            </p>
          )}
        </section>
      </div>
    </main>
  );
}
