// Number formatting in Indian (1,00,000) or international (100,000) grouping.
export function fmt(v: number | null | undefined, decimals = 2, style: "indian" | "international" = "indian"): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  if (Math.abs(v) < 0.5 * Math.pow(10, -decimals)) return "–";
  const locale = style === "indian" ? "en-IN" : "en-US";
  return new Intl.NumberFormat(locale, { minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(v);
}

export function pct(v: number | null | undefined, decimals = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "";
  return `${(v * 100).toFixed(decimals)}%`;
}

export const UNIT_LABEL: Record<string, string> = {
  rupees: "₹",
  thousand: "₹ '000",
  lakh: "₹ lakh",
  million: "₹ million",
  crore: "₹ crore",
};

export function dateTime(s: string | null | undefined): string {
  if (!s) return "";
  const d = new Date(s);
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function classOf(v: number | null | undefined): string {
  if (v === null || v === undefined) return "";
  return v < 0 ? "neg" : "";
}
