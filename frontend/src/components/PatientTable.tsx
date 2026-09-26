import type { Need, PatientRow } from "../types";
import { Empty, StatusBadge } from "./ui";

/**
 * Patient-centric view. Shows needs as well as tasks, deliberately: a need with
 * no task attached -- a patient with no PCP history -- is invisible on the
 * worklist but is exactly the population this system exists to surface.
 */
export function PatientTable({
  patients,
  total,
  onSelect,
}: {
  patients: PatientRow[];
  total: number;
  onSelect: (patientId: string) => void;
}) {
  if (!patients.length) {
    return <Empty>No patients match these filters.</Empty>;
  }

  return (
    <>
      <p className="result-count">
        {patients.length < total
          ? `Showing ${patients.length} of ${total} patients`
          : `${total} patient${total === 1 ? "" : "s"}`}
      </p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Patient</th>
              <th>Age</th>
              <th>Programs &amp; risk tier</th>
              <th>Needs</th>
              <th className="numeric">Tasks</th>
            </tr>
          </thead>
          <tbody>
            {patients.map(({ patient, enrollments, needs, tasks }) => (
              <tr key={patient.patient_id}>
                <td>
                  <button className="link-button" onClick={() => onSelect(patient.patient_id)}>
                    {patient.name}
                  </button>
                  <div className="sub">{patient.patient_id}</div>
                </td>
                <td>{patient.age ?? <span className="muted">unknown</span>}</td>
                <td>
                  {enrollments.length === 0 && <span className="muted">Not enrolled</span>}
                  {enrollments.map((e) => (
                    <div key={e.program_code} className="sub">
                      {e.program_name}:{" "}
                      <strong>{e.tier_name ?? "no tier matched"}</strong>
                    </div>
                  ))}
                </td>
                <td>
                  <div className="chips">
                    {sortNeeds(needs).map((need) => (
                      <span
                        key={`${need.program_code}-${need.target}`}
                        className="chip"
                        title={describeNeed(need)}
                      >
                        {need.target} <StatusBadge status={need.status} />
                      </span>
                    ))}
                  </div>
                </td>
                <td className="numeric">{tasks.length || <span className="muted">0</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

/** Open gaps first: the reason anyone is looking at this row. */
const ORDER = { DUE: 0, NEVER_SEEN: 1, SCHEDULED: 2, SATISFIED: 3 } as const;

function sortNeeds(needs: Need[]): Need[] {
  return [...needs].sort(
    (a, b) => ORDER[a.status] - ORDER[b.status] || a.target.localeCompare(b.target),
  );
}

function describeNeed(need: Need): string {
  const parts = [`${need.target} every ${need.cadence_days} days (${need.program_code})`];
  if (need.last_completed_date) parts.push(`last seen ${need.last_completed_date}`);
  else parts.push("no prior visit on record");
  if (need.next_scheduled_date) parts.push(`upcoming ${need.next_scheduled_date}`);
  if (need.days_overdue) parts.push(`${need.days_overdue} days overdue`);
  if (need.note) parts.push(need.note);
  return parts.join(" · ");
}
