import { BUILT_IN_MACHINES } from "./machines";
import { BUILT_IN_SCENARIOS } from "./scenarios";
import type { ComponentSpec, ComponentStatus, FaultScenario, MachineDefinition, MachineView } from "./types";

const machines = new Map<string, MachineDefinition>();
const scenarios = new Map<string, FaultScenario>();

/**
 * Add a machine and its fault scenarios to the library.
 *
 * Args:
 *   machine: Definition whose components reference library kinds.
 *   faults: Scenarios for this machine; each must name its components.
 *
 * Raises:
 *   Error: An id is already registered, or a scenario or mount names a
 *     component the machine does not have.
 */
export function registerMachine(machine: MachineDefinition, faults: FaultScenario[] = []) {
  if (machines.has(machine.id)) throw new Error(`Machine ${machine.id} is already registered.`);
  const ids = new Set<string>();
  for (const component of machine.components) {
    if (ids.has(component.id)) throw new Error(`${machine.id}: duplicate component ${component.id}.`);
    ids.add(component.id);
  }
  for (const component of machine.components) {
    if (component.mount && !ids.has(component.mount.component))
      throw new Error(`${machine.id}/${component.id}: unknown mount ${component.mount.component}.`);
  }
  for (const fault of faults) {
    if (scenarios.has(fault.id)) throw new Error(`Scenario ${fault.id} is already registered.`);
    const named = [fault.component, ...fault.related, ...fault.checks.flatMap((check) => (check.component ? [check.component] : []))];
    const missing = named.find((id) => !ids.has(id));
    if (fault.machine !== machine.id || missing)
      throw new Error(`Scenario ${fault.id}: ${missing ? `unknown component ${missing}` : `belongs to ${fault.machine}`}.`);
  }
  machines.set(machine.id, machine);
  for (const fault of faults) scenarios.set(fault.id, fault);
}

for (const machine of BUILT_IN_MACHINES)
  registerMachine(machine, BUILT_IN_SCENARIOS.filter((fault) => fault.machine === machine.id));

export function listMachines(): MachineDefinition[] {
  return [...machines.values()];
}

export function getMachine(id: string): MachineDefinition | undefined {
  return machines.get(id);
}

export function listScenarios(machine?: string): FaultScenario[] {
  return [...scenarios.values()].filter((fault) => !machine || fault.machine === machine);
}

export function getScenario(id: string | undefined): FaultScenario | undefined {
  return id ? scenarios.get(id) : undefined;
}

/** Components listed in the panel and pickable in 3D. */
export function selectableComponents(machine: MachineDefinition): ComponentSpec[] {
  return machine.components.filter((component) => component.selectable !== false);
}

/** Status of one component under a scenario: suspected, related or normal. */
export function componentStatus(fault: FaultScenario | undefined, component: string): ComponentStatus {
  if (!fault) return "normal";
  if (fault.component === component) return "fault";
  return fault.related.includes(component) ? "related" : "normal";
}

/** Resolve a view to its machine and scenario, ignoring unknown ids. */
export function resolveView(view: MachineView | null | undefined) {
  const machine = view ? getMachine(view.machine) : undefined;
  if (!machine) return null;
  const fault = getScenario(view?.scenario);
  return { machine, fault: fault?.machine === machine.id ? fault : undefined };
}
