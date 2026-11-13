# 3D machine diagnosis

Scope: the implemented front-end 3D viewer that shows a machine, highlights the
suspected component of a fault and links symptoms and checks to the model. It is
enabled with `VITE_MACHINE_DIAGNOSIS_VIZ=true` (see the
[frontend guide](../../frontend/README.md#3d-machine-diagnosis-panel)).

Machines are generic models built from a shared component library. Fault
scenarios, metrics and checks are **synthetic demo data**, not measurements,
alarm codes or an approved procedure. The agent has no diagnosis tool; the
browser chooses the view from message, answer and ticket text.

## What users can do

- Open the panel from chat: a message about a machine fault opens it beside the
  answer. A general request ("chẩn đoán giúp tôi") takes the machine and symptom
  from earlier questions in the chat. When the question names no fault, the
  agent's completed answer opens the panel if it names a specific fault.
- Open the standalone library at `/machines` (no chat needed). Add
  `?machine=<id>&scenario=<id>`, for example
  `/machines?machine=robot-cell&scenario=robot-grip`, to open a machine and fault.
- Open the mock repair-ticket workspace at `/machines/tickets` (see
  [Repair-ticket workspace](#repair-ticket-workspace-mock)).
- Open a ticket: the ticket dialog shows the machine and suspected component from
  the ticket's title and description, with an inline 3D view on request.
- Rotate, zoom and pan; click a component or its chip to select it; double-click
  to fly to it. The suspected component pulses red, related components amber.
- Read the selected component's kind and specification, the symptoms and a
  checklist. Each check has a button that selects and frames its component.
- Hide components the user does not want to see: the eye button on each
  component chip, or "Ẩn bộ phận này khỏi mô hình" under the selected
  component. Only that component's own meshes are hidden; parts mounted on it
  stay visible (hiding the CNC guard shows the machine inside). Hidden parts
  cannot be picked. The choice is saved per machine in this browser's
  `localStorage` (`machine-3d:hidden`); "Hiện tất cả" shows everything again.
- Switch machines, try another simulated scenario, turn x-ray view on to see
  inside guards and cabinets (automatic for interior parts), pause motion,
  expand the panel and show a performance readout (FPS, backend, draw calls,
  triangles, pixel ratio).

## Repair-ticket workspace (mock)

`/machines/tickets/:ticketId` shows one repair ticket beside the 3D model of its
machine. It works in mock and live builds and calls no backend: tickets come
from `createMockMachineTicketsApi` over four synthetic fixtures, and changes last
until the page reloads. Without an id the page opens the newest ticket.

| Area | Contents |
|---|---|
| Model (left) | Machine and location, view toolbar, the selected component, a colour legend and, after the repair, a "Khi báo lỗi / Sau khi sửa" switch. |
| Ticket (right) | Ticket switcher and status, title, the faulty device (name and fault; clicking the name flies the model to it), the reported description and one action button. Before diagnosis the device reads "Chưa xác định"; after the repair it shows what was done. |

Repair flow, one primary action at a time in the panel footer:

| Stage | Model | Action → next stage |
|---|---|---|
| `reported` | Machine without a highlighted fault | "Chạy chẩn đoán AI" → `diagnosed` (scan overlay, then the device is named and highlighted) |
| `diagnosed` | Suspect red, related parts amber | "Bắt đầu sửa" → `repairing` |
| `repairing` | Same | "Đánh dấu đã sửa" → `verifying` |
| `verifying` | Normal operation; before/after switch | "Xác nhận chạy ổn" → `closed`, or "Còn lỗi" → `repairing` |
| `closed` | Same | "Mở lại" → `repairing` |

The page opens on the whole machine; the device name and model clicks fly to a
component. X-ray turns on while a fault is
shown. Below 1024 px the model sits above the ticket and the footer stays at the
bottom of the screen.

To connect a backend, implement `MachineTicketsApi` (`list`, `get`, `act`) in `frontend/src/services/machine-tickets.ts` and pass it to
`MachineTicketsPage`. These tickets are separate from the expert inquiries under
`/tickets`.

## Built-in machines

| Machine | Main components | Scenarios |
|---|---|---|
| `cnc-vmc` vertical machining centre | bed and column, X/Y/Z ball-screw axes with servos, spindle and motor, 20-pocket tool changer, guard with doors, HMI, coolant unit, cabinet | spindle overheating, X-axis servo overload, tool change failure |
| `conveyor-line` sorting conveyor | 5.6 m belt, gear motor, photoelectric sensors, vision camera, pusher cylinder with reed switch, reject roller lane, cabinet with PLC and valves | jam at divert point, drive overload, pusher stroke, high reject rate |
| `robot-cell` pick-and-place cell | six-axis arm with J1–J3 servos, gripper and part sensor, infeed conveyor, fixture with clamp cylinders, valves, fence, light curtain, door switch, controller with PLC | J2 vibration, gripper drops parts, light-curtain safety stop |

## Code layout

| Location | Responsibility |
|---|---|
| `frontend/src/services/machine-library/` | Types, machine definitions, scenarios, registry and text resolver. No three.js; the panel reads it before the 3D chunk loads. |
| `frontend/src/components/machine-3d/library/` | One builder per component kind (`structure`, `motion`, `control`, `robot`). |
| `frontend/src/components/machine-3d/kit.ts` | Shared materials, cached geometry, instancing and LOD helpers. |
| `frontend/src/components/machine-3d/assemble.ts` | Builds a machine from its definition, merges static meshes, mounts components on sockets. |
| `frontend/src/components/machine-3d/viewer.ts` | Renderer, camera, picking, x-ray, highlight, adaptive quality and render loop. |
| `frontend/src/components/diagnosis/` | Chat panel, inline chat card and ticket view. |
| `frontend/src/pages/machines.tsx` | Standalone `/machines` page using the panel's `page` variant. |
| `frontend/src/pages/machine-tickets.tsx`, `components/machine-ticket/` | Mock repair-ticket workspace; `services/machine-tickets.ts` holds the flow and mock API, `fixtures/machine-tickets.ts` the synthetic tickets. |

## Add a machine

1. Describe it in a `MachineDefinition`: components with a `kind`, `label`,
   `group`, `params`, and either a floor `position`/`rotation` or a `mount` on
   another component's socket. Mark guards `shell: true` and parts inside them
   `interior: true`.
2. Add `FaultScenario` entries whose `component`, `related` and check components
   exist on the machine, with folded (diacritic-free, lower-case) `keywords`.
3. Call `registerMachine(machine, scenarios)`, or add both to the built-in lists
   in `machines.ts` and `scenarios.ts`. Registration rejects duplicate ids,
   unknown mounts and scenarios that name missing components.
4. Run `npm run test -- src/components/machine-3d src/services/machine-library`.
   The assembly test checks mounts, real-world scale and the render budget for
   every registered machine.

Units are metres. Floor items stand on y = 0 with their front toward +z.

## Add a component kind

1. Write a builder `(params, kit) => { object, sockets?, update?, setStatus? }`
   in `components/machine-3d/library/`. Use `kit.box`, `kit.cyl`, shared
   finishes (`kit.mat`) and `kit.instances` for repeated parts.
2. Mark moving groups with `pivot()` so their meshes are not merged into the
   static body. Expose attachment points with `socket()` and document the
   orientation a mounted part expects.
3. Add the kind to `ComponentKind`, `KIND_LABELS` and `COMPONENT_BUILDERS`.

## Performance

- Rendering uses WebGPU when the browser offers an adapter and WebGL otherwise.
  `?renderer=webgl` or `?renderer=webgpu` forces a backend for comparison.
- three.js and the viewer load only when a 3D view opens; the WebGPU renderer
  is a separate chunk loaded only on WebGPU browsers.
- Static meshes are merged per material within each rigid part; fences, rollers,
  legs, keys, I/O modules and tool pockets are instanced; motor fins use LOD.
- Built-in machines are 50–62 k triangles and 88–132 meshes. Tests fail above
  250 k triangles or 400 meshes per machine.
- Rendering stops while the tab is hidden or the viewer is off-screen. The pixel
  ratio starts at up to 2× and steps down when the frame rate falls below 45 FPS;
  below 30 FPS at 1× shadows turn off.
- Motion is limited to belts, products, the robot cycle, cylinders, the spindle,
  the tool changer and status lamps. It is off under `prefers-reduced-motion`.

Measured in the Claude desktop app's browser pane on the development machine,
1440 × 900 viewport, panel docked, 2× pixel ratio: CNC on WebGPU 120 FPS with
214 draw calls; conveyor on WebGL 120 FPS with 150 draw calls (the display's
refresh limit). This is not a measurement on a MacBook Pro M3.

## Limitations

- Models are generic equipment of the stated type, not a scan or CAD model of a
  particular factory machine; proportions approximate common designs.
- Keyword matching selects the machine and scenario. It can miss phrasing or
  pick the wrong fault; the panel labels every result as a simulated hypothesis.
- The diagnosis state is kept in the browser tab only and is not saved with the
  conversation or ticket.
- In the mock `/ask` demo, the first question of a new chat navigates to a new
  thread and the panel opens only from the second question.
