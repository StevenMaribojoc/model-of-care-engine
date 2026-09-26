import type { Meta, Role } from "../types";

/**
 * Every dropdown here is populated from /api/meta, which is generated from the
 * rule configuration. Adding a program or a specialty to the YAML makes it
 * appear in these controls with no change to this file -- which is what keeps
 * "adding a program is a config edit" honest.
 */

interface Props {
  meta: Meta;
  role: Role;
  values: {
    specialty: string;
    task_type: string;
    program: string;
    tier: string;
    need_status: string;
    search: string;
  };
  showNeedStatus: boolean;
  onChange: (patch: Record<string, string>) => void;
  onReset: () => void;
}

export function Filters({ meta, role, values, showNeedStatus, onChange, onReset }: Props) {
  // Task type options are limited to what this role can see. The server enforces
  // this regardless; offering a scheduler a "Referral" option that always
  // returns nothing would just be a confusing UI.
  const allowedTaskTypes = meta.roles.find((r) => r.code === role)?.task_types ?? [];

  const tiers = meta.programs
    .filter((p) => !values.program || p.code === values.program)
    .flatMap((p) => p.tiers.map((t) => ({ ...t, programName: p.name })));

  const hasFilters =
    values.specialty || values.task_type || values.program || values.tier ||
    values.need_status || values.search;

  return (
    <div className="filters">
      <label>
        <span>Search</span>
        <input
          type="search"
          placeholder="Name or patient ID"
          value={values.search}
          onChange={(e) => onChange({ search: e.target.value })}
        />
      </label>

      <label>
        <span>Specialty</span>
        <select
          value={values.specialty}
          onChange={(e) => onChange({ specialty: e.target.value })}
        >
          <option value="">All specialties</option>
          {meta.specialties.map((s) => (
            <option key={s.code} value={s.code}>
              {s.name}
            </option>
          ))}
        </select>
      </label>

      <label>
        <span>Task type</span>
        <select
          value={values.task_type}
          onChange={(e) => onChange({ task_type: e.target.value })}
        >
          <option value="">
            {allowedTaskTypes.length > 1 ? "All visible types" : "Scheduling only"}
          </option>
          {allowedTaskTypes.map((t) => (
            <option key={t} value={t}>
              {t === "REFERRAL" ? "Referral" : "Scheduling"}
            </option>
          ))}
        </select>
      </label>

      <label>
        <span>Program</span>
        <select
          value={values.program}
          onChange={(e) => onChange({ program: e.target.value, tier: "" })}
        >
          <option value="">All programs</option>
          {meta.programs.map((p) => (
            <option key={p.code} value={p.code}>
              {p.name}
            </option>
          ))}
        </select>
      </label>

      <label>
        <span>Risk tier</span>
        <select value={values.tier} onChange={(e) => onChange({ tier: e.target.value })}>
          <option value="">All tiers</option>
          {tiers.map((t) => (
            <option key={`${t.program_code}-${t.code}`} value={t.code}>
              {t.name}
              {values.program ? "" : ` (${t.programName})`}
            </option>
          ))}
        </select>
      </label>

      {showNeedStatus && (
        <label>
          <span>Need status</span>
          <select
            value={values.need_status}
            onChange={(e) => onChange({ need_status: e.target.value })}
          >
            <option value="">Any open gap</option>
            {meta.need_statuses.map((s) => (
              <option key={s} value={s}>
                {s === "NEVER_SEEN" ? "Never seen" : s.charAt(0) + s.slice(1).toLowerCase()}
              </option>
            ))}
          </select>
        </label>
      )}

      {hasFilters && (
        <button type="button" className="link-button" onClick={onReset}>
          Clear filters
        </button>
      )}
    </div>
  );
}
