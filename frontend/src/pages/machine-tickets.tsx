import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Loader2, TriangleAlert } from "lucide-react";
import type { SceneApi } from "@/components/machine-3d/machine-scene";
import { TicketPanel } from "@/components/machine-ticket/ticket-panel";
import { TicketViewer, type CompareMode } from "@/components/machine-ticket/ticket-viewer";
import { resolveView } from "@/services/machine-library/registry";
import {
  actionsFor,
  createMockMachineTicketsApi,
  isRepaired,
  showsFault,
  type MachineTicket,
  type MachineTicketsApi,
  type RepairAction,
} from "@/services/machine-tickets";
import "@/components/diagnosis/diagnosis.css";
import "@/components/machine-ticket/machine-ticket.css";

const defaultApi = createMockMachineTicketsApi();

/** Shortest time the diagnosis scan stays on screen, so the step reads as work. */
export const DIAGNOSE_MIN_MS = 1400;

const NOTICES: Record<RepairAction, string> = {
  diagnose: "Đã tìm thấy thiết bị lỗi trên mô hình.",
  start: "Đã nhận việc sửa.",
  fix: "Mô hình đang hiển thị máy sau khi sửa.",
  verify: "Đã xác nhận máy chạy bình thường. Ticket hoàn tất.",
  reopen: "Đã mở lại ticket.",
};

const BASE = "/machines/tickets";

/** The workspace viewer is large, so focusing keeps more of the machine in view than the chat panel. */
const FOCUS = { minDistance: 3.2 };

/**
 * Mock repair-ticket workspace at `/machines/tickets/:ticketId`.
 *
 * The 3D model of the ticket's machine fills the left side; the ticket sits
 * on the right with the faulty device and the reported description. Diagnosis
 * highlights the device, its name flies the camera to it, and marking the
 * repair done switches the model to normal operation with a before/after
 * comparison. Data comes from
 * `MachineTicketsApi` (in-memory mock by default) and is synthetic.
 */
export function MachineTicketsPage({ api = defaultApi }: { api?: MachineTicketsApi }) {
  const { ticketId } = useParams();
  const navigate = useNavigate();
  const [tickets, setTickets] = useState<MachineTicket[] | null>(null);
  const [loadError, setLoadError] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [compare, setCompare] = useState<CompareMode>("after");
  const [xrayChoice, setXrayChoice] = useState<boolean | null>(null);
  const [motion, setMotion] = useState(() => !(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false));
  const [busy, setBusy] = useState<RepairAction | null>(null);
  const [notice, setNotice] = useState("");
  const [sceneError, setSceneError] = useState("");
  const [openedId, setOpenedId] = useState<string | null>(null);
  const scene = useRef<SceneApi | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .list()
      .then((items) => !cancelled && setTickets(items))
      .catch(() => !cancelled && setLoadError("Không tải được danh sách ticket."));
    return () => {
      cancelled = true;
    };
  }, [api]);

  const ticket = tickets?.find((item) => item.id === ticketId) ?? null;
  const resolved = useMemo(() => (ticket ? resolveView(ticket.view) : null), [ticket]);
  const machine = resolved?.machine;
  const fault = resolved?.fault;

  useEffect(() => {
    if (tickets?.length && !ticketId) navigate(`${BASE}/${tickets[0].id}`, { replace: true });
  }, [tickets, ticketId, navigate]);

  if (ticket && ticket.id !== openedId) {
    // Opening another ticket selects its faulty device once diagnosed.
    setOpenedId(ticket.id);
    setSelected(showsFault(ticket.stage) ? fault?.component ?? null : null);
    setCompare("after");
    setXrayChoice(null);
    setNotice("");
  }

  // A ticket opens on the whole machine; the suspect's marker shows where to look.
  // Same-machine tickets keep the viewer, so its camera is reset here.
  useEffect(() => {
    if (openedId) scene.current?.resetView();
  }, [openedId]);

  if (loadError)
    return (
      <div className="mt-page mt-page-message" role="alert">
        <TriangleAlert size={20} /> {loadError}
      </div>
    );
  // Without an id the page redirects to the newest ticket once the list loads.
  if (!tickets || (!ticketId && tickets.length))
    return (
      <div className="mt-page mt-page-message" aria-busy="true">
        <Loader2 size={20} className="animate-spin" /> Đang tải ticket…
      </div>
    );
  if (!ticket || !machine)
    return (
      <div className="mt-page mt-page-message">
        {tickets.length ? "Không tìm thấy ticket này." : "Chưa có ticket."}
        {tickets.length > 0 && (
          <button type="button" className="mt-button secondary" onClick={() => navigate(`${BASE}/${tickets[0].id}`)}>
            Mở ticket mới nhất
          </button>
        )}
      </div>
    );

  const diagnosing = busy === "diagnose";
  const repaired = isRepaired(ticket.stage);
  const shownFault = diagnosing ? undefined : showsFault(ticket.stage) || (repaired && compare === "before") ? fault : undefined;
  const selectedSpec = machine.components.find((component) => component.id === selected);
  // Guards hide the parts a diagnosis points to, so x-ray is on while a fault or an
  // interior part is shown, unless the user chose otherwise.
  const xray = xrayChoice ?? (!!shownFault || !!selectedSpec?.interior);

  const select = (id: string | null, focus = true) => {
    setSelected(id);
    if (id && focus) scene.current?.focus(id, FOCUS);
  };
  const replace = (next: MachineTicket) => setTickets((items) => items?.map((item) => (item.id === next.id ? next : item)) ?? items);

  async function act(action: RepairAction) {
    if (!ticket) return;
    setBusy(action);
    setNotice("");
    try {
      const [next] = await Promise.all([
        api.act(ticket.id, action),
        action === "diagnose" ? new Promise((resolve) => setTimeout(resolve, DIAGNOSE_MIN_MS)) : null,
      ]);
      replace(next);
      if ((action === "diagnose" || action === "reopen") && fault) select(fault.component);
      if (action === "fix") {
        setCompare("after");
        setSelected(null);
        scene.current?.resetView();
      }
      setNotice(NOTICES[action]);
    } catch {
      setNotice("Thao tác thất bại. Hãy thử lại.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mt-page" data-testid="machine-tickets-page">
      <TicketViewer
        machine={machine}
        location={ticket.location}
        fault={shownFault}
        selected={selectedSpec}
        xray={xray}
        motion={motion}
        compare={repaired ? compare : null}
        diagnosing={diagnosing}
        error={sceneError}
        apiRef={scene}
        onSelect={select}
        onCompare={(mode) => {
          setCompare(mode);
          if (mode === "before" && fault) select(fault.component);
          else setSelected(null);
        }}
        onXray={setXrayChoice}
        onMotion={setMotion}
        onError={setSceneError}
      />
      <TicketPanel
        tickets={tickets}
        ticket={ticket}
        machine={machine}
        fault={fault}
        actions={actionsFor(ticket.stage)}
        busy={busy}
        notice={notice}
        onOpenTicket={(id) => navigate(`${BASE}/${id}`)}
        onLocate={(id) => select(id)}
        onAction={(action) => void act(action)}
      />
    </div>
  );
}
