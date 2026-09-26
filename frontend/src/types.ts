/** Mirrors the API response models in backend/app/api/schemas.py. */

export type Role = "SCHEDULER" | "CLINICAL";
export type TaskType = "SCHEDULING" | "REFERRAL";
export type NeedStatus = "SATISFIED" | "SCHEDULED" | "DUE" | "NEVER_SEEN";

export interface PatientSummary {
  patient_id: string;
  name: string;
  age: number | null;
  gender: string | null;
  language: string | null;
  phone: string | null;
  pcp_provider_name: string | null;
}

export interface Enrollment {
  program_code: string;
  program_name: string;
  tier_code: string | null;
  tier_name: string | null;
  tier_priority: number | null;
  evidence: Record<string, unknown>;
}

export interface Need {
  program_code: string;
  need_type: string;
  target: string;
  cadence_days: number;
  status: NeedStatus;
  last_completed_date: string | null;
  next_scheduled_date: string | null;
  due_date: string | null;
  days_overdue: number | null;
  note: string | null;
}

export interface Task {
  task_id: number;
  patient_id: string;
  patient_name: string;
  task_type: TaskType;
  need_type: string;
  target: string;
  priority: number;
  due_date: string | null;
  days_overdue: number | null;
  status: string;
  program_codes: string[];
  patient: PatientSummary | null;
}

export interface PatientRow {
  patient: PatientSummary;
  enrollments: Enrollment[];
  needs: Need[];
  tasks: Task[];
}

export interface PatientDetail extends PatientRow {
  history: {
    diagnoses: {
      icd_code: string;
      description: string | null;
      diagnosed_date: string | null;
    }[];
    labs: {
      test_name: string;
      result_value: number;
      result_date: string;
      after_as_of: boolean;
    }[];
    encounters: {
      specialty: string;
      encounter_date: string;
      provider_name: string | null;
      upcoming: boolean;
    }[];
  };
}

export interface RunInfo {
  run_id: number;
  as_of: string;
  rules_version: string;
  patient_count: number;
  duration_ms: number;
  warnings: string[];
}

export interface Page<T> {
  total: number;
  limit: number;
  offset: number;
  items: T[];
  run: RunInfo;
}

export interface Summary {
  run: RunInfo;
  role: Role;
  total_patients: number;
  patients_with_tasks: number;
  tasks_by_type: Record<string, number>;
  tasks_by_specialty: Record<string, number>;
  enrollments_by_tier: Record<string, number>;
  needs_by_status: Record<string, number>;
  unactionable_gaps: number;
}

export interface Meta {
  default_as_of: string;
  rules_version: string;
  programs: {
    code: string;
    name: string;
    description: string | null;
    is_active: boolean;
    tiers: {
      code: string;
      name: string;
      priority: number;
      program_code: string;
      needs: {
        need_type: string;
        target: string;
        cadence_days: number;
        note: string | null;
      }[];
    }[];
  }[];
  specialties: { code: string; name: string; requires_referral: boolean }[];
  need_statuses: NeedStatus[];
  roles: { code: Role; name: string; task_types: TaskType[] }[];
}
