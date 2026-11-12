export type Role = "admin" | "analyst" | "approver" | "viewer";

export interface Me {
  id: number;
  email: string;
  name: string;
  role: Role;
  is_superadmin: boolean;
  permissions: string[];
  tenant: { id: number; name: string; license_plan: string; license_expires: string | null };
  companies: { id: number; code: string; name: string; is_demo: boolean }[];
}

export interface CompanySettings {
  calc_unit_divisor: number;
  display_unit: string;
  number_format: "indian" | "international";
  decimals: number;
  fiscal_year_start_month: number;
  mis_tolerance: number;
  ignore_columns: string[];
  meta: Record<string, string | null>;
  [k: string]: unknown;
}

export interface Company {
  id: number;
  code: string;
  name: string;
  currency: string;
  is_demo: boolean;
  settings: CompanySettings;
}

export interface UploadRef {
  id: number;
  filename: string;
  status: string;
  uploaded_at: string | null;
}

export interface Period {
  id: number;
  code: string;
  label: string;
  scenario: "actual" | "budget" | "forecast";
  basis: string;
  fiscal_year: string | null;
  start_date: string | null;
  end_date: string;
  months: number;
  status: "open" | "locked";
  uploads: Record<string, UploadRef>;
}

export interface Control {
  code: string;
  level: "ok" | "warning" | "error";
  title: string;
  amount: number;
  details: Record<string, unknown>[];
}

export interface Run {
  id: number;
  company_id: number;
  period_id: number;
  period_code: string;
  period_label: string;
  scenario: string;
  status: string;
  note: string | null;
  created_at: string | null;
  duration_s: number | null;
  control_summary: { errors?: number; warnings?: number; ok?: number };
  error: string | null;
  started_by: string | null;
  submitted_by: string | null;
  submitted_at: string | null;
  approved_by: string | null;
  approved_at: string | null;
  review_comment: string | null;
  timings: Record<string, number>;
  controls?: Control[];
  plants?: string[];
  product_groups?: string[];
  com_code?: string;
  calc_unit_divisor?: number;
  finished_at?: string | null;
  inputs?: Record<string, unknown>;
}

export interface ReportColumn {
  plant: string;
  code: string;
  label: string;
  family: string;
  memo?: boolean;
}

export interface ReportRow {
  kind: "line" | "header" | "blank";
  label: string;
  line?: string;
  style: { bold?: boolean; fill?: string | null; italic?: boolean; indent?: number };
  values?: number[];
  pct?: (number | null)[];
  ratio?: boolean;
}

export interface Report {
  report: string;
  title: string;
  unit: string;
  company: string;
  currency: string;
  period: { code: string; label: string; scenario: string };
  run: { id: number; status: string };
  columns: ReportColumn[];
  rows: ReportRow[];
}

export interface Kpis {
  sales?: number;
  material_cost?: number;
  material_cost_pct?: number | null;
  gross_margin?: number;
  gross_margin_pct?: number | null;
  plant_profit?: number;
  plant_profit_pct?: number | null;
  pbt?: number;
  pbt_pct?: number | null;
  bep?: number;
  bep_per_month?: number;
  [k: string]: number | null | undefined | string;
}

export interface AuditEntry {
  id: number;
  created_at: string;
  user_email: string | null;
  action: string;
  entity: string;
  entity_id: string | null;
  company_id: number | null;
  details: Record<string, unknown>;
}
