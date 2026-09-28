import { useState } from "react";
import { api, type Filters } from "../api";
import type { Task, TaskState } from "../types";

/**
 * The one place a human can write anything into this system.
 *
 * PROTOTYPE. It exists to demonstrate one property: what a person types is
 * stored against the natural key of the work -- this patient, this action,
 * this specialty -- and not against the task's row id. Task rows are destroyed
 * and rebuilt on every engine run, so an id-based link would lose the note the
 * first time the engine re-ran.
 *
 * Note what is missing: a "completed" option. Completion is a clinical fact.
 * The visit lands in the encounter feed, the need becomes satisfied, and the
 * task stops being generated. A tick box here would let the worklist drift
 * away from what actually happened to the patient.
 */

// Stand-ins. A real deployment would read these from the directory that already
// governs sign-in, not from a hardcoded list.
const STAFF = ["Maria Alvarez", "Devon Park", "Priya Nair", "Sam Okafor"];

const STATUSES = [
  { value: "OPEN", label: "Open" },
  { value: "IN_PROGRESS", label: "In progress" },
  { value: "SNOOZED", label: "Snoozed" },
];

export function TaskStateControl({
  task,
  filters,
  onSaved,
}: {
  task: Task;
  filters: Filters;
  onSaved: (state: TaskState) => void;
}) {
  const [assignee, setAssignee] = useState(task.state?.assignee ?? "");
  const [status, setStatus] = useState(task.state?.status ?? "OPEN");
  const [note, setNote] = useState(task.state?.note ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty =
    assignee !== (task.state?.assignee ?? "") ||
    status !== (task.state?.status ?? "OPEN") ||
    note !== (task.state?.note ?? "");

  async function save() {
    setSaving(true);
    setError(null);
    try {
      onSaved(await api.setTaskState(task.task_id, filters, { status, assignee, note }));
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="task-state">
      <div className="task-state-row">
        <label>
          <span>Assigned to</span>
          <select value={assignee} onChange={(e) => setAssignee(e.target.value)}>
            <option value="">Unassigned</option>
            {STAFF.map((person) => (
              <option key={person} value={person}>{person}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Status</span>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUSES.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
        </label>
        <button type="button" onClick={save} disabled={!dirty || saving}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
      <label className="task-state-note">
        <span>Note</span>
        <input
          type="text"
          placeholder="e.g. called 9/27, left voicemail"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </label>
      {error && <p className="error">{error}</p>}
      {task.state?.updated_at && !dirty && (
        <p className="sub">
          Last updated {task.state.updated_at.slice(0, 16).replace("T", " ")}
          {task.state.updated_by_role ? ` by ${task.state.updated_by_role.toLowerCase()}` : ""}
        </p>
      )}
    </div>
  );
}
