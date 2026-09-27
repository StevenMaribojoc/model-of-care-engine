import type { NeedStatus, TaskType } from "../types";

/** Small shared presentation pieces. Colour carries meaning, not decoration. */

const NEED_STATUS_CLASS: Record<NeedStatus, string> = {
  DUE: "badge badge-due",
  NEVER_SEEN: "badge badge-never",
  SCHEDULED: "badge badge-scheduled",
  SATISFIED: "badge badge-satisfied",
};

const NEED_STATUS_LABEL: Record<NeedStatus, string> = {
  DUE: "Due",
  NEVER_SEEN: "Never seen",
  SCHEDULED: "Scheduled",
  SATISFIED: "Up to date",
};

export function StatusBadge({ status }: { status: NeedStatus }) {
  return <span className={NEED_STATUS_CLASS[status]}>{NEED_STATUS_LABEL[status]}</span>;
}

export function TaskTypeBadge({ type }: { type: TaskType }) {
  return (
    <span className={type === "REFERRAL" ? "badge badge-referral" : "badge badge-scheduling"}>
      {type === "REFERRAL" ? "Referral" : "Scheduling"}
    </span>
  );
}

/**
 * Task urgency, labelled P1/P2/P3 rather than High/Medium/Routine.
 *
 * The words matter here. A tier is literally named "High Priority", and a task
 * inherits its number from whichever tier demanded it -- so a patient in "High
 * Priority" wellness produces a P2 task, while their diabetes tier produces P1.
 * Labelling that P2 task "Medium" made it look like the row contradicted the
 * patient's tier. P1/P2/P3 cannot be mistaken for a tier name, and the row now
 * names its driving tier alongside (see WorklistTable).
 */
export function PriorityDot({ priority }: { priority: number }) {
  const rank = Math.min(priority, 3);
  return (
    <span
      className={`priority priority-${rank}`}
      title={`Task priority ${priority} — inherited from the risk tier that requires this care`}
    >
      P{priority}
    </span>
  );
}

export function Overdue({ days }: { days: number | null }) {
  if (days === null) return <span className="muted">—</span>;
  return <span className="overdue">{days.toLocaleString()}d</span>;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}

/** Renders tier evidence as readable text instead of raw JSON. */
export function Evidence({ evidence }: { evidence: Record<string, unknown> }) {
  const parts: string[] = [];
  if (typeof evidence.age === "number") parts.push(`age ${evidence.age}`);
  if (Array.isArray(evidence.matched_codes) && evidence.matched_codes.length) {
    parts.push(`dx ${(evidence.matched_codes as string[]).join(", ")}`);
  }
  if (evidence.test && evidence.value != null) {
    parts.push(`${evidence.test} ${evidence.value} on ${evidence.result_date}`);
  } else if (evidence.test && evidence.value == null) {
    const last = evidence.last_result_date
      ? `last ${evidence.last_result_date}`
      : "no result on record";
    parts.push(`no recent ${evidence.test} (${last})`);
  }
  if (!parts.length) return <span className="muted">—</span>;
  return <span className="evidence">{parts.join(" · ")}</span>;
}
