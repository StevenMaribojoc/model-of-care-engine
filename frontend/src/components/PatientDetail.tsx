import { useEffect, useState } from "react";
import { api, type Filters } from "../api";
import type { PatientDetail as Detail } from "../types";
import { TaskStateControl } from "./TaskStateControl";
import { Evidence, Overdue, StatusBadge, TaskTypeBadge } from "./ui";

/**
 * The "why is this patient here?" drawer.
 *
 * Every engine decision shown next to the raw facts that produced it: the tier
 * with its evidence, each need with the dates it was derived from, and the
 * underlying diagnoses, labs, and encounters. A rules engine whose output staff
 * cannot interrogate is one they will quietly stop trusting, so this view is
 * not a nicety.
 */
export function PatientDetail({
  patientId,
  filters,
  onClose,
}: {
  patientId: string;
  filters: Filters;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError(null);
    api
      .patient(patientId, filters)
      .then((d) => !cancelled && setDetail(d))
      .catch((e) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, [patientId, filters.role, filters.as_of]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <header>
          <div>
            <h2>{detail ? detail.patient.name : patientId}</h2>
            {detail && (
              <p className="sub">
                {detail.patient.patient_id} · {detail.patient.age ?? "unknown"} years ·{" "}
                {detail.patient.gender ?? "—"} · {detail.patient.language ?? "—"}
                <br />
                Assigned PCP: {detail.patient.pcp_provider_name ?? "none on file"}
              </p>
            )}
          </div>
          <button className="link-button" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        {error && <p className="error">{error}</p>}
        {!detail && !error && <p className="muted">Loading…</p>}

        {detail && (
          <div className="drawer-body">
            <section>
              <h3>Programs and risk tier</h3>
              {detail.enrollments.length === 0 && (
                <p className="muted">Not eligible for any active program.</p>
              )}
              {detail.enrollments.map((e) => (
                <div key={e.program_code} className="panel">
                  <strong>{e.program_name}</strong> —{" "}
                  {e.tier_name ?? <em>no tier matched</em>}
                  <div className="sub">
                    Why: <Evidence evidence={e.evidence} />
                  </div>
                </div>
              ))}
            </section>

            <section>
              <h3>Clinical needs</h3>
              <table className="compact">
                <thead>
                  <tr>
                    <th>Need</th>
                    <th>Status</th>
                    <th>Cadence</th>
                    <th>Last</th>
                    <th>Upcoming</th>
                    <th>Due</th>
                    <th>Overdue</th>
                    <th>Program</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.needs.map((n) => (
                    <tr key={`${n.program_code}-${n.target}`}>
                      <td>
                        {n.target}
                        {n.note && <div className="sub">{n.note}</div>}
                      </td>
                      <td>
                        <StatusBadge status={n.status} />
                      </td>
                      <td>{n.cadence_days}d</td>
                      <td>{n.last_completed_date ?? <span className="muted">never</span>}</td>
                      <td>{n.next_scheduled_date ?? "—"}</td>
                      <td>{n.due_date ?? "—"}</td>
                      <td>
                        <Overdue days={n.days_overdue} />
                      </td>
                      <td className="sub">{n.program_code}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            <section>
              <h3>Tasks visible to this role</h3>
              {detail.tasks.length === 0 ? (
                <p className="muted">
                  No tasks for this role. A gap with no task means no prior primary care
                  history, which the spec routes to nobody.
                </p>
              ) : (
                <ul className="plain">
                  {detail.tasks.map((t) => (
                    <li key={t.task_id} className="task-item">
                      <div>
                        <TaskTypeBadge type={t.task_type} /> {t.target} · due {t.due_date ?? "—"}{" "}
                        <span className="sub">({t.program_codes.join(", ")})</span>
                      </div>
                      {/* The only writable thing in the app. Everything above it
                          is derived and read-only. */}
                      <TaskStateControl
                        task={t}
                        filters={filters}
                        onSaved={(state) =>
                          setDetail((current) =>
                            current === null
                              ? current
                              : {
                                  ...current,
                                  tasks: current.tasks.map((x) =>
                                    x.task_id === t.task_id ? { ...x, state } : x,
                                  ),
                                },
                          )
                        }
                      />
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="history">
              <div>
                <h3>Diagnoses</h3>
                <ul className="plain">
                  {detail.history.diagnoses.map((d, i) => (
                    <li key={i}>
                      <code>{d.icd_code}</code> {d.description}
                      <span className="sub"> · {d.diagnosed_date ?? "undated"}</span>
                    </li>
                  ))}
                  {!detail.history.diagnoses.length && <li className="muted">None on file</li>}
                </ul>
              </div>

              <div>
                <h3>Labs</h3>
                <ul className="plain">
                  {detail.history.labs.map((l, i) => (
                    <li key={i} className={l.after_as_of ? "muted" : undefined}>
                      {l.test_name} <strong>{l.result_value}</strong>
                      <span className="sub">
                        {" "}
                        · {l.result_date}
                        {/* Shown but greyed: a result the engine could not see is
                            part of the explanation, not noise. */}
                        {l.after_as_of ? " (after evaluation date)" : ""}
                      </span>
                    </li>
                  ))}
                  {!detail.history.labs.length && <li className="muted">None on file</li>}
                </ul>
              </div>

              <div>
                <h3>Encounters</h3>
                <ul className="plain">
                  {detail.history.encounters.map((e, i) => (
                    <li key={i}>
                      {e.specialty}
                      <span className="sub">
                        {" "}
                        · {e.encounter_date}
                        {e.upcoming ? " (upcoming)" : ""}
                        {e.provider_name ? ` · ${e.provider_name}` : ""}
                      </span>
                    </li>
                  ))}
                  {!detail.history.encounters.length && <li className="muted">None on file</li>}
                </ul>
              </div>
            </section>
          </div>
        )}
      </aside>
    </div>
  );
}
