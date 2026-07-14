/**
 * Role-aware access for Miya swarm tools.
 * Managers (copilot) vs staff (companion) — never invent elevated access.
 */

export type MizanAudience = "manager" | "staff" | "unknown";

const MANAGER_ROLES = new Set([
  "MANAGER",
  "ADMIN",
  "OWNER",
  "SUPER_ADMIN",
  "RESTAURANT_OWNER",
  "GENERAL_MANAGER",
]);

/** Tools that must not run for staff companions (manager copilot only). */
export const MANAGER_ONLY_TOOLS = new Set([
  "sales_report",
  "square_pos",
  "supplier_order",
  "grant_role",
  "hr_lifecycle",
  "dashboard_widgets",
  "cash_reconciliation",
  "record_invoice",
  "mark_invoice_paid",
  "create_shifts_by_role",
  "optimize_schedule",
  "labor_report",
  "cross_location_report",
  "get_proactive_insights",
  "smart_report",
]);

export function normalizeRole(role: unknown): string {
  return String(role || "")
    .trim()
    .toUpperCase()
    .replace(/\s+/g, "_");
}

export function isManagerRole(role: unknown): boolean {
  const r = normalizeRole(role);
  if (!r) return false;
  if (MANAGER_ROLES.has(r)) return true;
  // Job titles that often mean floor staff — not managers
  if (["WAITER", "CHEF", "COOK", "BARMAN", "BARTENDER", "HOST", "CASHIER", "STAFF"].includes(r)) {
    return false;
  }
  return r.includes("MANAGER") || r.includes("ADMIN") || r.includes("OWNER");
}

export function audienceFromRole(role: unknown): MizanAudience {
  const r = normalizeRole(role);
  if (!r) return "unknown";
  return isManagerRole(r) ? "manager" : "staff";
}

export function isManagerDashboardChannel(channel: string): boolean {
  return /luapop|dashboard|webchat|embed|web\b/i.test(channel || "");
}

/**
 * Resolve audience for this turn.
 * Dashboard channels default to manager when role unknown (LuaPop is manager UX).
 * WhatsApp defaults to staff when role unknown (safer).
 */
export function resolveAudience(opts: {
  role?: unknown;
  channel?: string;
  cachedAudience?: unknown;
}): MizanAudience {
  const cached = String(opts.cachedAudience || "").toLowerCase();
  if (cached === "manager" || cached === "staff") return cached;

  const fromRole = audienceFromRole(opts.role);
  if (fromRole !== "unknown") return fromRole;

  if (isManagerDashboardChannel(opts.channel || "")) return "manager";
  return "staff";
}

export function managerOnlyDeniedMessage(toolName?: string): string {
  const tip = toolName ? ` (${toolName})` : "";
  return (
    `That action${tip} is only available for managers. ` +
    `If you need something from your manager, say *Tell my manager…* and I'll pass it on.`
  );
}

export function assertManagerToolAccess(opts: {
  toolName: string;
  role?: unknown;
  channel?: string;
  cachedAudience?: unknown;
}): { ok: true } | { ok: false; message: string } {
  if (!MANAGER_ONLY_TOOLS.has(opts.toolName)) return { ok: true };
  const audience = resolveAudience(opts);
  if (audience === "manager") return { ok: true };
  if (audience === "unknown" && isManagerDashboardChannel(opts.channel || "")) {
    return { ok: true };
  }
  return { ok: false, message: managerOnlyDeniedMessage(opts.toolName) };
}

export function readCachedRole(user: {
  data?: Record<string, unknown>;
  _luaProfile?: Record<string, unknown>;
}): string {
  const data = user.data || {};
  const profile = user._luaProfile || {};
  const meta =
    profile.metadata && typeof profile.metadata === "object"
      ? (profile.metadata as Record<string, unknown>)
      : {};
  return normalizeRole(
    data.role || data.mizanRole || meta.role || profile.role || "",
  );
}
