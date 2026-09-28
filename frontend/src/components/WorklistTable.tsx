import type { Task } from "../types";
import { Empty, Overdue, PriorityDot, TaskTypeBadge } from "./ui";

function formatStatus(status: string): string {
  return status === "IN_PROGRESS" ? "In progress" : status.charAt(0) + status.slice(1).toLowerCase();
}

/** Task-centric view: one row is one piece of work for one staff member. */
export function WorklistTable({
  tasks,
  total,
  onSelect,
}: {
  tasks: Task[];
  total: number;
  onSelect: (patientId: string) => void;
}) {
  if (!tasks.length) {
    return <Empty>No tasks match these filters for this role.</Empty>;
  }

  return (
    <>
      <p className="result-count">
        {tasks.length < total
          ? `Showing ${tasks.length} of ${total} tasks`
          : `${total} task${total === 1 ? "" : "s"}`}
        {/* Says it out loud, because a patient with two care gaps legitimately
            occupies two rows here and that reads as a duplicate otherwise. */}
        <span className="sub"> — one row per task, so a patient may appear more than once</span>
      </p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Priority</th>
              <th>Patient</th>
              <th>Action</th>
              <th>Specialty</th>
              <th>Due</th>
              <th>Overdue</th>
              <th>Who has it</th>
              <th>Driven by</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((task) => (
              <tr key={task.task_id}>
                <td>
                  <PriorityDot priority={task.priority} />
                </td>
                <td>
                  <button className="link-button" onClick={() => onSelect(task.patient_id)}>
                    {task.patient_name}
                  </button>
                  <div className="sub">
                    {task.patient_id}
                    {task.patient?.language && task.patient.language !== "English"
                      ? ` · ${task.patient.language}`
                      : ""}
                  </div>
                </td>
                <td>
                  <TaskTypeBadge type={task.task_type} />
                </td>
                <td>{task.target}</td>
                <td>{task.due_date ?? "—"}</td>
                <td>
                  <Overdue days={task.days_overdue} />
                </td>
                <td>
                  {/* Human-owned. Blank for almost every row, because nobody has
                      picked it up yet. */}
                  {task.state?.assignee ? (
                    <>
                      {task.state.assignee}
                      <div className="sub">{formatStatus(task.state.status)}</div>
                    </>
                  ) : (
                    <span className="muted">—</span>
                  )}
                  {task.state?.note && <div className="sub note">"{task.state.note}"</div>}
                </td>
                <td className="sub">
                  {/* Names the tier, not just the program: the priority column
                      inherits its number from this tier, so showing them side by
                      side is what makes "P2" legible. Plural when one appointment
                      serves more than one program. */}
                  {task.sources.length
                    ? task.sources.map((source) => (
                        <div key={source.program_code}>
                          {source.program_name}
                          {source.tier_name ? ` · ${source.tier_name}` : ""}
                        </div>
                      ))
                    : task.program_codes.join(", ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
