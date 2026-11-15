// Icons from Lucide (lucide.dev, ISC licence): one professionally drawn, consistent set across the app.
import {
  Archive, ArrowLeftRight, Bell, Calculator, ChartPie, Check, ChevronDown, Database, Download, Factory, FileChartColumn,
  FileDown, FileSpreadsheet, FileText, FileUp, History, IndianRupee, Layers, LayoutDashboard, ListChecks, ListFilter,
  LogOut, Menu, Monitor, Moon, Package, PanelLeft, Percent, Play, Plus, Presentation, Save, Search, ShieldCheck,
  SlidersHorizontal, Sun, Table2, Target, TrendingUp, TriangleAlert, Upload, User, UserCog, Wallet, X,
  type LucideIcon,
} from "lucide-react";

const ICONS = {
  dashboard: LayoutDashboard,
  sheet: Table2,
  tb: FileSpreadsheet,
  upload: Upload,
  fileup: FileUp,
  adjust: SlidersHorizontal,
  calc: Calculator,
  reports: FileChartColumn,
  compare: ArrowLeftRight,
  masters: Database,
  audit: History,
  admin: UserCog,
  menu: Menu,
  user: User,
  logout: LogOut,
  sun: Sun,
  moon: Moon,
  monitor: Monitor,
  bell: Bell,
  play: Play,
  download: Download,
  filedown: FileDown,
  rupee: IndianRupee,
  trend: TrendingUp,
  factory: Factory,
  percent: Percent,
  box: Package,
  target: Target,
  check: Check,
  alert: TriangleAlert,
  layers: Layers,
  chevron: ChevronDown,
  plus: Plus,
  x: X,
  filter: ListFilter,
  save: Save,
  sparkle: ListChecks,
  shield: ShieldCheck,
  search: Search,
  panel: PanelLeft,
  wallet: Wallet,
  pie: ChartPie,
  doc: FileText,
  ppt: Presentation,
  zip: Archive,
} satisfies Record<string, LucideIcon>;

export type IconName = keyof typeof ICONS;

export function Icon({ name, size = 18, className, strokeWidth = 1.75 }: { name: IconName; size?: number; className?: string; strokeWidth?: number }) {
  const C = ICONS[name];
  return <C className={className} size={size} strokeWidth={strokeWidth} aria-hidden focusable="false" />;
}
