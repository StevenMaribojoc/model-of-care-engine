import type { Task } from "../types";
import { Empty, Overdue, PriorityDot, TaskTypeBadge } from "./ui";

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
                <td className="sub">
                  {/* Plural when one appointment serves more than one program. */}
                  {task.program_codes.join(", ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
