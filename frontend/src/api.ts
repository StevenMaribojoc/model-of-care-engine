import type { Meta, Page, PatientDetail, PatientRow, Summary, Task, TaskState } from "./types";

/**
 * Thin fetch wrapper.
 *
 * Note what is NOT here: any filtering of tasks by role. The client sends the
 * role and the server decides what comes back. If this file tried to hide
 * referral tasks from a scheduler it would be security theatre, because anyone
 * can open the network tab.
 */
async function get<T>(path: string, params: Record<string, string | undefined>): Promise<T> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, value);
  }
  const response = await fetch(`/api${path}?${query}`);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText} on ${path}`);
  }
  return response.json() as Promise<T>;
}

export type Filters = {
  role: string;
  as_of?: string;
  specialty?: string;
  task_type?: string;
  program?: string;
  tier?: string;
  need_status?: string;
  search?: string;
};

/**
 * The only write in the app. Everything else is derived and read-only, so this
 * is the single place a person can change anything.
 */
async function patch<T>(path: string, params: Record<string, string | undefined>, body: unknown): Promise<T> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, value);
  }
  const response = await fetch(`/api${path}?${query}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText} on ${path}`);
  return response.json() as Promise<T>;
}

export const api = {
  meta: () => get<Meta>("/meta", {}),
  summary: (f: Filters) => get<Summary>("/summary", { role: f.role, as_of: f.as_of }),
  tasks: (f: Filters, limit = 200) =>
    get<Page<Task>>("/tasks", { ...f, limit: String(limit) }),
  patients: (f: Filters, limit = 200) =>
    get<Page<PatientRow>>("/patients", { ...f, limit: String(limit) }),
  patient: (id: string, f: Filters) =>
    get<PatientDetail>(`/patients/${id}`, { role: f.role, as_of: f.as_of }),
  setTaskState: (
    taskId: number,
    f: Filters,
    body: { status?: string; assignee?: string; note?: string },
  ) => patch<TaskState>(`/tasks/${taskId}/state`, { role: f.role, as_of: f.as_of }, body),
};
