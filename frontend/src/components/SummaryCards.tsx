import type { Summary } from "../types";

export function SummaryCards({ summary }: { summary: Summary }) {
  const scheduling = summary.tasks_by_type.SCHEDULING ?? 0;
  const referral = summary.tasks_by_type.REFERRAL ?? 0;
  const isScheduler = summary.role === "SCHEDULER";

  return (
    <div className="cards">
      <Card
        label={isScheduler ? "Appointments to book" : "Open tasks"}
        value={scheduling + referral}
        detail={
          isScheduler
            ? "scheduling only"
            : `${scheduling} scheduling · ${referral} referral`
        }
      />
      <Card
        label="Patients with work"
        value={summary.patients_with_tasks}
        detail={`of ${summary.total_patients} in the population`}
      />
      <Card
        label="Gaps with no task"
        value={summary.unactionable_gaps}
        /* Called out rather than buried: these patients have a real care gap
           that appears on nobody's worklist, which is precisely the failure
           mode the system is meant to expose. */
        detail="no prior PCP visit — routed to nobody"
        muted
      />
      <Card
        label="Evaluated as of"
        value={summary.run.as_of}
        detail={`run ${summary.run.run_id} · ${summary.run.duration_ms}ms · rules ${summary.run.rules_version.slice(0, 8)}`}
      />
    </div>
  );
}

function Card({
  label,
  value,
  detail,
  muted,
}: {
  label: string;
  value: number | string;
  detail: string;
  muted?: boolean;
}) {
  return (
    <div className={muted ? "card card-muted" : "card"}>
      <div className="card-label">{label}</div>
      <div className="card-value">{value}</div>
      <div className="card-detail">{detail}</div>
    </div>
  );
}
