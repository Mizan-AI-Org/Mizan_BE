/**
 * Manage the manager's dashboard widgets — the cards shown on
 * `/dashboard`. Each widget is a **live command-centre lane** that
 * streams categorised requests, tasks, and operational data in real time.
 *
 * THE COMMAND-CENTRE FLOW:
 * 1. Manager tells Miya "create an Operations widget" →
 *    Miya calls `add` with `operations` → a live Operations lane appears
 *    on the dashboard, listing every day-to-day operations query from staff
 *    (cleaning, process changes, floor issues, etc.).
 * 2. Similarly: "create a Finance widget" → `add` with `finance` →
 *    all invoices, bills, payables appear on this card.
 * 3. "Create an HR widget" → `add` with `human_resources` → all HR
 *    queries, complaints, document requests appear on this card.
 * 4. Staff requests filed via WhatsApp are AUTO-CATEGORISED by Miya
 *    (using intent classification) and routed to the correct widget.
 *    The manager sees each category's items perfectly sorted.
 *
 * Miya uses this whenever the manager says things like:
 *   - "Add the Meetings & Reminders widget"
 *   - "Remove the Wellbeing card from my dashboard"
 *   - "Put Operations above Staffing"
 *   - "Create a Purchases widget" → must add the data-bound `purchase_orders`
 *     built-in (NOT a placeholder shortcut)
 *   - "Create a shortcut to my custom report PDF" → genuine shortcut tile
 *   - "Delete the shortcut I made last week"
 *   - "What widgets do I have now?"
 *   - "Ajoute le widget de réservations" / "احذف بطاقة الحضور"
 *
 * Supports seven actions:
 *   - list          → fetch the manager's current widget layout + the
 *                     catalogue of valid built-in ids (Miya should call
 *                     this first when unsure what's available).
 *   - add           → add one or more built-in widgets (e.g. `operations`,
 *                     `meetings_reminders`, `clock_ins`, `purchase_orders`).
 *                     **THIS IS THE ACTION TO USE FOR OPERATIONAL CATEGORY
 *                     WIDGETS** (Purchases / HR / Finance / Maintenance /
 *                     Urgent / Inbox / Clock-ins / Inventory / etc.) — they
 *                     are already wired to the live request stream and
 *                     show real data. Each widget is an operations &
 *                     actions command centre for that category.
 *   - remove        → remove one or more widgets (built-in ids or
 *                     `custom:<uuid>` slots from `create_custom`).
 *   - reorder       → replace the full widget order.
 *   - create_custom → create a new shortcut tile (title + link + icon).
 *                     ONLY USE FOR REAL SHORTCUTS that don't have a
 *                     built-in equivalent (e.g. "shortcut to a Google
 *                     Sheet"). If the manager asks for a Purchases / HR /
 *                     Finance / Maintenance / Urgent / Calendar / Inbox /
 *                     Clock-in / Inventory widget, use `add` with the
 *                     matching built-in id instead — those are the ones
 *                     that show live data. As a safety net, the backend
 *                     auto-redirects aliased titles ("Purchases", "HR",
 *                     "Achats", …) to the matching built-in `add`, but
 *                     picking the right action up-front is faster.
 *   - delete_custom → permanently delete a shortcut tile Miya created
 *                     earlier.
 *   - create_category → tenant-wide **section / rubrique** for grouping
 *                     custom shortcut tiles (Add-widget dialog). Idempotent
 *                     by name.
 *
 * The built-in widget id space is small and fixed. When the manager uses
 * natural language ("the clocking widget"), pick the closest id:
 *     insights, tasks_demands, staffing, sales_or_tasks, operations,
 *     wellbeing, live_attendance, compliance_risk, inventory_delivery,
 *     task_execution, take_orders, reservations, retail_store_ops,
 *     jobsite_crew, ops_reports, staff_inbox, staff_messages,
 *     meetings_reminders, clock_ins, incidents, urgent_top,
 *     human_resources, finance, maintenance, purchase_orders,
 *     miscellaneous, team_travel.
 *
 * Title → built-in id quick map (memorise these — they are the most
 * common manager-driven asks):
 *     "Operations" / "Ops" / "Day-to-day"  → operations
 *     "Purchases" / "PO" / "Procurement"   → purchase_orders
 *     "HR" / "Human Resources" / "RH"      → human_resources
 *     "Finance" / "Bills" / "Invoices"     → finance
 *     "Maintenance" / "Repairs"            → maintenance
 *     "Urgent" / "Top urgent"              → urgent_top
 *     "Inbox" / "Staff requests"           → staff_inbox
 *     "Team Travel" / "Leave" / "Time off" / "Travel requests" → team_travel
 *     "Staff messages" / "WhatsApp"        → staff_messages
 *     "Meetings" / "Calendar" / "Reminders"→ meetings_reminders
 *     "Clock in" / "Attendance" (arrivals list) → clock_ins
 *     "Attendance widget" / "Live attendance" / "Who is here" → live_attendance
 *     "Incidents"                          → incidents
 *     "Inventory" / "Stock" / "Deliveries" → inventory_delivery
 *     "Tasks" / "To-do"                    → tasks_demands
 *     "Misc" / "Other" / "Divers"          → miscellaneous
 *
 * When in doubt, call `list` first and match against `allowed_builtin_ids`.
 */

import { LuaTool, User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { resolveAgentContext, resolveMizanUserIdFromUser } from "../../services/agentContext";
import {
    noContextError,
    validationError,
    upstreamError,
} from "./_common/errors";
import { resolveDashboardWidgetIntent, resolveOperationalWidgetFromPhrase } from "./dashboardWidgetIntent";

/** LLM often sends human labels instead of snake_case ids; backend also normalizes. */
const DASHBOARD_WIDGET_ID_SYNONYMS: Record<string, string> = {
    attendance: "clock_ins",
    attendances: "clock_ins",
    clock_in: "clock_ins",
    clockin: "clock_ins",
    clockins: "clock_ins",
    clocking: "clock_ins",
    pointage: "clock_ins",
    pointages: "clock_ins",
    liveattendance: "live_attendance",
    live_attendance: "live_attendance",
};

function normalizeDashboardWidgetId(raw: string): string {
    const st = raw.trim();
    if (!st) return st;
    if (st.toLowerCase().startsWith("custom:")) return st;
    const key = st
        .toLowerCase()
        .replace(/\s+/g, "_")
        .replace(/-/g, "_")
        .split("_")
        .filter(Boolean)
        .join("_");
    return DASHBOARD_WIDGET_ID_SYNONYMS[key] ?? key;
}

export default class DashboardWidgetsTool implements LuaTool {
    name = "dashboard_widgets";
    description =
        "Manage the manager's dashboard widgets — each widget is a LIVE COMMAND CENTRE lane showing categorised staff requests, tasks, and operational data. " +
        "When the manager says 'create an Operations widget', Miya adds the `operations` built-in and all day-to-day operations queries from staff " +
        "are listed perfectly on this widget. Similarly for Finance, HR, Maintenance, etc. — each is a focused command centre. " +
        "Use this whenever the manager says 'add/remove/reorder widget', " +
        "'create a widget/shortcut on my dashboard', 'delete the shortcut I made', 'show me my dashboard layout', 'put X before/after Y', " +
        "'create a new section/rubrique/category for my shortcuts', or any " +
        "language equivalent (FR: 'ajoute/enlève/réordonne le widget', 'crée un widget', 'nouvelle rubrique', 'nouvelle section'; ES: 'agrega/quita el widget'; PT: 'adiciona o widget'; " +
        "AR: 'أضف/احذف/رتب البطاقة'; Darija: 'zid/7iyed widget'). Exactly one `action` must be set: 'list' (fetch current layout + allowed ids), " +
        "'add' (add one or more built-in widgets), 'remove' (remove built-in ids OR 'custom:<uuid>' slots from the layout), 'reorder' (replace the " +
        "full order), 'create_custom' (create a new shortcut tile with title + optional subtitle + optional link + optional icon), 'delete_custom' " +
        "(permanently delete a shortcut tile), or 'create_category' (tenant-wide dashboard section for grouping tiles — FR **rubrique**). Built-in widget ids: insights, tasks_demands, staffing, sales_or_tasks, operations, wellbeing, " +
        "live_attendance, compliance_risk, inventory_delivery, task_execution, take_orders, reservations, retail_store_ops, jobsite_crew, ops_reports, " +
        "staff_inbox, staff_messages, meetings_reminders, clock_ins, incidents, urgent_top, human_resources, finance, maintenance, purchase_orders, miscellaneous, team_travel. " +
        "*** CRITICAL — operational lanes vs shortcuts ***. " +
        "If the manager asks for a widget that maps to a known operational lane, you MUST use action='add' with the matching built-in id (NEVER create_custom). " +
        "These built-ins are already wired to the live request/task stream and show real data; create_custom would just put a 'Ask Miya' placeholder over them. " +
        "Required title→builtin map: 'Purchases'/'PO'/'Procurement'/'Achats'/'مشتريات' → purchase_orders; " +
        "'HR'/'Human Resources'/'RH'/'Ressources humaines'/'موارد بشرية' → human_resources; " +
        "'Finance'/'Bills'/'Invoices'/'Factures'/'مالية' → finance; " +
        "'Maintenance'/'Repairs'/'Entretien'/'صيانة' → maintenance; " +
        "'Urgent'/'Top urgent'/'Urgences'/'عاجل' → urgent_top; " +
        "'Inbox'/'Staff requests'/'Demandes du personnel' → staff_inbox; " +
        "'Staff messages'/'WhatsApp messages' → staff_messages; " +
        "'Meetings'/'Calendar'/'Reminders'/'Réunions'/'اجتماعات' → meetings_reminders; " +
        "'Clock-in'/'Attendance' (clock events) → clock_ins; 'Attendance widget'/'Live attendance'/'Who is here' → live_attendance; " +
        "'Pointage'/'حضور' → clock_ins; " +
        "'Incidents' → incidents; 'Inventory'/'Stock'/'Deliveries'/'Inventaire'/'مخزون' → inventory_delivery; " +
        "'Tasks'/'To-do'/'Tâches'/'مهام' → tasks_demands; 'Misc'/'Other'/'Divers'/'متفرقات' → miscellaneous. " +
        "'Leave requests'/'Team leave'/'Time off'/'Team travel'/'Team retreat'/'Congé' → team_travel (scheduling command centre). " +
        "Only fall back to create_custom for genuine shortcut tiles that have no built-in equivalent (e.g. 'shortcut to a Google Sheet I keep' or 'shortcut to /reports/sales'). " +
        "If you accidentally call create_custom with one of the operational titles above, the backend will auto-redirect to the matching `add` and respond with `resolved_from_alias: true` — relay the returned message_for_user verbatim. " +
        "The 'incidents' widget shows the top 5 most-recent reported incidents and links to the Reported Incidents page. Custom tiles use a 'custom:<uuid>' slot id — 'list' returns both the slot id and the tile's title so " +
        "you can echo it back. When the manager refers to a widget by free-text name, pick the closest id (fuzzy match by label), or call 'list' first. " +
        "CRITICAL for create_custom: `link_url` is OPTIONAL — NEVER ask the user for a URL. If they don't provide one, omit it and the backend will " +
        "auto-resolve a sensible in-app route from the title (or leave it blank, which is still valid). Same for `subtitle` (use the manager's own " +
        "description if they gave one, otherwise omit) and `icon` (omit to use the default). As soon as you have a title, CALL the tool — do not ask " +
        "follow-up questions. After the call, relay the tool's `message_for_user` verbatim in the conversation language. NEVER call any other dashboard " +
        "tool to confirm — this tool already returns the updated `order`.";

    inputSchema = z.object({
        action: z
            .enum([
                "list",
                "add",
                "remove",
                "reorder",
                "create_custom",
                "delete_custom",
                "create_category",
            ])
            .describe(
                "Required. 'list' to read the current layout, 'add' / 'remove' / 'reorder' to edit the built-in widget order, " +
                    "'create_custom' to create a new shortcut tile, 'delete_custom' to remove a shortcut tile entirely, " +
                    "'create_category' to create a dashboard section (rubrique) for grouping shortcuts."
            ),
        widgets: z
            .array(z.string())
            .optional()
            .describe(
                "Used by 'add' and 'remove'. Array of widget ids. For built-ins pass the plain id (e.g. 'operations'). For custom tiles pass the 'custom:<uuid>' slot."
            ),
        order: z
            .array(z.string())
            .optional()
            .describe(
                "Used by 'reorder'. The full ordered list of widget ids Miya wants on the dashboard. Unknown ids are dropped silently."
            ),
        title: z
            .string()
            .max(255)
            .optional()
            .describe(
                "Used by 'create_custom'. Short title shown on the dashboard tile (e.g. 'Sales report shortcut')."
            ),
        subtitle: z
            .string()
            .max(2000)
            .optional()
            .describe("Used by 'create_custom'. One-line helper text under the title."),
        link_url: z
            .string()
            .optional()
            .describe(
                "Used by 'create_custom'. OPTIONAL. An in-app path (e.g. '/dashboard/reports') or a full https URL. " +
                    "NEVER ask the user for a URL — if they didn't explicitly provide one, OMIT this field. The backend will auto-resolve a route from the title; if nothing matches, the tile is still created with an empty link and works as a labelled placeholder."
            ),
        icon: z
            .string()
            .optional()
            .describe(
                "Used by 'create_custom'. One of the allowed icon slugs: sparkles, clipboard-check, list-todo, calendar, users, package, shopping-cart, file-text, bar-chart-2, clipboard-list, hard-hat, store, inbox, activity, shield-alert, clock, heart, calendar-days, layout-grid. Defaults to 'sparkles'."
            ),
        add_to_dashboard: z
            .boolean()
            .optional()
            .describe(
                "Used by 'create_custom'. Default true — adds the new tile to the manager's layout immediately. Set false only if the manager said 'create it but don't add it yet'."
            ),
        category_name: z
            .string()
            .optional()
            .describe(
                "Used by 'create_custom' (optional group for the tile) OR **required** for 'create_category' — the new section name (e.g. 'EVENT SOPHIE KASABAH')."
            ),
        order_index: z
            .number()
            .int()
            .optional()
            .describe("Used by 'create_category'. Sort order in the category list (default 0)."),
        widget_id: z
            .string()
            .optional()
            .describe(
                "Used by 'delete_custom'. The DashboardCustomWidget UUID, or a 'custom:<uuid>' slot id — either works."
            ),
        user_id: z
            .string()
            .optional()
            .describe(
                "Target manager's user UUID. Omit to default to the current conversation user — the backend resolves from the agent context."
            ),
        email: z
            .string()
            .optional()
            .describe("Fallback: target manager email if user_id is unknown."),
        phone: z
            .string()
            .optional()
            .describe("Fallback: target manager phone (digits or +…)."),
        restaurantId: z
            .string()
            .optional()
            .describe(
                "ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]. Do NOT omit."
            ),
    });

    constructor(private apiService: ApiService = new ApiService()) {}

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get();
        if (!user) return noContextError();

        // Client-side alias resolution (mirrors backend widget_alias_resolver.py).
        let action = input.action;
        let widgets = input.widgets;
        let title = input.title;

        if (action === "create_custom" && title?.trim()) {
            const probes = [
                input.subtitle?.trim(),
                title.trim(),
                `create a ${title.trim()} widget`,
            ].filter(Boolean) as string[];
            for (const probe of probes) {
                const resolved =
                    resolveDashboardWidgetIntent(probe) ??
                    resolveOperationalWidgetFromPhrase(probe);
                if (resolved?.action === "add") {
                    action = "add";
                    widgets = resolved.widgets;
                    break;
                }
            }
        }

        const ctx = await resolveAgentContext(input.restaurantId);
        if (!ctx.restaurantId) return noContextError();
        const rid = ctx.restaurantId;

        const userData = (user as any)?.data || {};
        const profile = (user as any)?._luaProfile || {};
        // Mizan UUID lives in user.data.userId (auth webhook / tenant preprocessor), not user.data.id / Lua's opaque id.
        const targetUserId =
            input.user_id || ctx.userId || resolveMizanUserIdFromUser(user) || undefined;

        const common = {
            user_id: targetUserId,
            email:
                input.email ||
                ctx.email ||
                userData.email ||
                userData.emailAddress ||
                profile.email ||
                profile.emailAddress,
            phone: input.phone || ctx.phone,
        };

        if (!common.user_id && !common.email && !common.phone) {
            return validationError(
                "I need your Mizan account on file to change dashboard widgets. Open Miya from the logged-in Mizan dashboard, or ensure your Lua profile is linked (manager email / phone)."
            );
        }

        if (action === "add" || action === "remove") {
            if (!widgets || widgets.length === 0) {
                return validationError(
                    "Tell me which widget id(s) to " +
                        action +
                        " — e.g. 'operations', 'meetings_reminders'. Call `dashboard_widgets` with action='list' first if you're unsure."
                );
            }
        }
        if (action === "reorder") {
            if (!input.order || input.order.length === 0) {
                return validationError(
                    "Pass the full desired order as `order: [id, id, id, …]`."
                );
            }
        }
        if (action === "create_custom") {
            if (!title || !title.trim()) {
                return validationError(
                    "A `title` is required to create a shortcut tile (e.g. 'Sales report')."
                );
            }
        }
        if (action === "delete_custom") {
            if (!input.widget_id || !input.widget_id.trim()) {
                return validationError(
                    "Pass the `widget_id` to delete — either the raw UUID or the 'custom:<uuid>' slot id from a previous `list` call."
                );
            }
        }
        if (action === "create_category") {
            const cn = (input.category_name || "").trim();
            if (!cn) {
                return validationError(
                    "Pass `category_name` with the new dashboard section name (e.g. EVENT SOPHIE KASABAH). In French this is often called a **rubrique**."
                );
            }
        }

        const widgetsForApi =
            action === "add" || action === "remove"
                ? widgets?.map((w) => normalizeDashboardWidgetId(w))
                : widgets;
        const orderForApi =
            action === "reorder"
                ? input.order?.map((w) => normalizeDashboardWidgetId(w))
                : input.order;

        const payload: Parameters<
            ApiService["manageDashboardWidgetsForAgent"]
        >[2] = {
            ...common,
            widgets: widgetsForApi,
            order: orderForApi,
            title: title?.trim(),
            subtitle: input.subtitle,
            source_text: input.subtitle || title?.trim(),
            link_url: input.link_url,
            icon: input.icon,
            add_to_dashboard: input.add_to_dashboard,
            category_name: input.category_name,
            widget_id: input.widget_id,
            order_index: input.order_index,
        };

        let result: Awaited<ReturnType<InstanceType<typeof ApiService>["manageDashboardWidgetsForAgent"]>>;
        try {
            result = await this.apiService.manageDashboardWidgetsForAgent(rid, action, payload);
        } catch (error: any) {
            const em = String(error?.message || error || "");
            if (/Buffer|ArrayBuffer|first argument must be of type string/i.test(em)) {
                return upstreamError(
                    "A network encoding error occurred while updating the dashboard. Please refresh Mizan and try again, or contact support if it persists.",
                );
            }
            throw error;
        }

        if (!result || result.success === false) {
            const human = result?.error || "";
            if (/agent key not configured|no agent key configured/i.test(human)) {
                return {
                    status: "error" as const,
                    code: "NOT_AUTHORIZED" as const,
                    message: human,
                    miya_directive:
                        "Tell the manager (in their language) that dashboard widget changes aren't wired up on the server yet — the ops team needs to set LUA_WEBHOOK_API_KEY on the Mizan backend and redeploy Miya. Do NOT blame the manager.",
                };
            }
            const looksLikeBackendText =
                human && !/^(<|\{|status|econn|request)/i.test(human);
            if (looksLikeBackendText) {
                return {
                    status: "error" as const,
                    code: "VALIDATION" as const,
                    message: human,
                    miya_directive:
                        "Relay this as-is in the user's conversation language. Do not add technical jargon; it is a friendly explanation of what went wrong (e.g. invalid widget id, user not a manager).",
                };
            }
            return upstreamError(human);
        }

        return {
            status: "success" as const,
            action,
            // When the backend recognises an operational alias (e.g.
            // "Purchases" → purchase_orders) it transparently swaps a
            // create_custom call for an `add` of the matching built-in
            // widget. The flag tells the persona to phrase the
            // confirmation as "added the live X lane" instead of
            // "created a shortcut".
            resolved_from_alias: (result as any).resolved_from_alias === true,
            alias_input: (result as any).alias_input,
            message: result.message_for_user,
            miya_directive:
                "Relay the message field VERBATIM. Do NOT say 'temporary technical issue' or invent your own apology.",
            category: (result as any).category,
            created: (result as any).created,
            order: result.order,
            order_detail: result.order_detail,
            custom_widgets: result.custom_widgets,
            allowed_builtin_ids: result.allowed_builtin_ids,
            removed: result.removed,
            dropped: result.dropped,
            added: (result as any).added,
            widget: result.widget,
            widget_id: result.widget_id,
        };
    }
}
