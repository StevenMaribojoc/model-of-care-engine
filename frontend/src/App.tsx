import { useEffect, useMemo, useState } from "react";
import { api, type Filters as ApiFilters } from "./api";
import { Filters } from "./components/Filters";
import { PatientDetail } from "./components/PatientDetail";
import { PatientTable } from "./components/PatientTable";
import { SummaryCards } from "./components/SummaryCards";
import { WorklistTable } from "./components/WorklistTable";
import type { Meta, Page, PatientRow, Role, Summary, Task } from "./types";
import { useUrlState } from "./useUrlState";

const DEFAULTS = {
  role: "CLINICAL",
  tab: "worklist",
  as_of: "",
  specialty: "",
  task_type: "",
  program: "",
  tier: "",
  need_status: "",
  search: "",
  patient: "",
};

export default function App() {
  const [state, setState] = useUrlState(DEFAULTS);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [tasks, setTasks] = useState<Page<Task> | null>(null);
  const [patients, setPatients] = useState<Page<PatientRow> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const role = state.role as Role;

  const filters: ApiFilters = useMemo(
    () => ({
      role: state.role,
      as_of: state.as_of || undefined,
      specialty: state.specialty || undefined,
      task_type: state.task_type || undefined,
      program: state.program || undefined,
      tier: state.tier || undefined,
      need_status: state.need_status || undefined,
      search: state.search || undefined,
    }),
    [state],
  );

  useEffect(() => {
    api.meta().then(setMeta).catch((e) => setError(String(e)));
  }, []);

  // Switching to Scheduler while filtered to referrals would leave the user
  // staring at an empty table and blaming the app rather than the filter. The
  // server would correctly return nothing; this just keeps the UI coherent.
  useEffect(() => {
    if (!meta) return;
    const allowed = meta.roles.find((r) => r.code === role)?.task_types ?? [];
    if (state.task_type && !allowed.includes(state.task_type as never)) {
      setState({ task_type: "" });
    }
  }, [meta, role, state.task_type, setState]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    const request =
      state.tab === "worklist"
        ? api.tasks(filters).then((page) => !cancelled && setTasks(page))
        : api.patients(filters).then((page) => !cancelled && setPatients(page));

    Promise.all([request, api.summary(filters).then((s) => !cancelled && setSummary(s))])
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false));

    return () => {
      cancelled = true;
    };
  }, [filters, state.tab]);

  const datasetDate = meta?.default_as_of ?? "";
  const asOfValue = state.as_of || datasetDate;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <h1>Model of Care Engine</h1>
          <span className="sub">Population health worklists</span>
        </div>

        <div className="topbar-controls">
          <div className="role-switch" role="group" aria-label="Worklist role">
            {(meta?.roles ?? []).map((r) => (
              <button
                key={r.code}
                className={role === r.code ? "active" : ""}
                onClick={() => setState({ role: r.code })}
                title={`Sees: ${r.task_types.join(", ")}`}
              >
                {r.name}
              </button>
            ))}
          </div>

          <label className="asof">
            <span>Evaluate as of</span>
            <input
              type="date"
              value={asOfValue}
              onChange={(e) => setState({ as_of: e.target.value })}
            />
            {state.as_of && state.as_of !== datasetDate && (
              <button className="link-button" onClick={() => setState({ as_of: "" })}>
                reset
              </button>
            )}
          </label>
        </div>
      </header>

      {/* Makes the role contract explicit rather than leaving the user to infer
          why the two views differ. */}
      <p className="role-note">
        {role === "SCHEDULER"
          ? "Scheduler view: patients who have seen this specialty before, so an appointment can be booked directly. Referrals are hidden."
          : "Clinical team view: scheduling tasks plus referrals, which need a clinician to decide whether a referral is appropriate before booking."}
      </p>

      {summary && <SummaryCards summary={summary} />}

      <nav className="tabs">
        <button
          className={state.tab === "worklist" ? "active" : ""}
          onClick={() => setState({ tab: "worklist" })}
        >
          Worklist
        </button>
        <button
          className={state.tab === "patients" ? "active" : ""}
          onClick={() => setState({ tab: "patients" })}
        >
          Patients
        </button>
      </nav>

      {meta && (
        <Filters
          meta={meta}
          role={role}
          values={{
            specialty: state.specialty,
            task_type: state.task_type,
            program: state.program,
            tier: state.tier,
            need_status: state.need_status,
            search: state.search,
          }}
          showNeedStatus={state.tab === "patients"}
          onChange={setState}
          onReset={() =>
            setState({
              specialty: "",
              task_type: "",
              program: "",
              tier: "",
              need_status: "",
              search: "",
            })
          }
        />
      )}

      {error && <p className="error">{error}</p>}

      <main className={loading ? "loading" : undefined}>
        {state.tab === "worklist" && tasks && (
          <WorklistTable
            tasks={tasks.items}
            total={tasks.total}
            onSelect={(id) => setState({ patient: id })}
          />
        )}
        {state.tab === "patients" && patients && (
          <PatientTable
            patients={patients.items}
            total={patients.total}
            onSelect={(id) => setState({ patient: id })}
          />
        )}
      </main>

      {state.patient && (
        <PatientDetail
          patientId={state.patient}
          filters={filters}
          onClose={() => setState({ patient: "" })}
        />
      )}
    </div>
  );
}
