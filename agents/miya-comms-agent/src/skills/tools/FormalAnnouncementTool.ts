/**
 * Send a formal announcement to staff (app + WhatsApp) with an optional
 * audience filter.
 *
 * Audience kinds:
 *   - "all"         — every active staff member at the restaurant.
 *   - "roles"       — formal job titles (CHEF, WAITER, MANAGER, …).
 *   - "tags"        — canonical operational tags (KITCHEN, SERVICE,
 *                     FRONT_OFFICE, BACK_OFFICE, PURCHASES, CONTROL,
 *                     ADMINISTRATION, MANAGEMENT, HOUSEKEEPING,
 *                     MARKETING). Use these for "announce to the
 *                     kitchen", "let all housekeeping know …".
 *   - "departments" — free-text StaffProfile.department values.
 *   - "staff_ids"   — explicit UUID list when the manager picked them.
 */
import { LuaTool, User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { noContextError } from "./_common/errors";

function getRestaurantId(user: any) {
    const userData = user?.data || {};
    const profile = (user as any)?._luaProfile || {};
    return (user as any)?.restaurantId || userData.restaurantId || profile.restaurantId || (profile.metadata && (profile.metadata as any).restaurantId);
}

function normaliseTag(value: string | null | undefined): string | null {
    if (!value) return null;
    const cleaned = String(value).trim().toUpperCase().replace(/[-\s]+/g, "_").replace(/_+/g, "_");
    return cleaned || null;
}

export default class FormalAnnouncementTool implements LuaTool {
    name = "send_announcement";
    description =
        "Send a formal announcement to staff (in-app and WhatsApp). Audience can be 'all', specific roles (CHEF, WAITER), canonical tags (KITCHEN, SERVICE, FRONT_OFFICE, BACK_OFFICE, PURCHASES, CONTROL, ADMINISTRATION, MANAGEMENT, HOUSEKEEPING, MARKETING), departments, or explicit staff_ids. Use this when the manager wants a broadcast-style message ('Announce: we're closed tomorrow', 'Announce to the kitchen: new menu starts Monday'). For a quick one-off WhatsApp to a named person or crew, prefer inform_staff.";

    inputSchema = z.object({
        message: z.string().describe("The announcement text."),
        title: z.string().optional().describe("Short title; default 'Announcement'."),
        audience: z
            .enum(["all", "roles", "staff_ids", "tags", "departments"])
            .optional()
            .default("all")
            .describe("Who receives: all, roles, staff_ids, tags, or departments."),
        roles: z.array(z.string()).optional().describe("If audience=roles: e.g. ['CHEF','WAITER']."),
        staff_ids: z.array(z.string()).optional().describe("If audience=staff_ids: list of staff UUIDs."),
        tags: z
            .array(z.string())
            .optional()
            .describe(
                "If audience=tags: canonical operational tags. Any-of match. Allowed: KITCHEN, SERVICE, FRONT_OFFICE, BACK_OFFICE, PURCHASES, CONTROL, ADMINISTRATION, MANAGEMENT, HOUSEKEEPING, MARKETING.",
            ),
        departments: z
            .array(z.string())
            .optional()
            .describe("If audience=departments: StaffProfile.department strings (case-insensitive exact match)."),
        restaurantId: z
            .string()
            .optional()
            .describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]. Do NOT omit."),
    });

    constructor(private apiService: ApiService = new ApiService()) {}

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get();
        if (!user) return { status: "error", message: "No context." };
        const rid = input.restaurantId || getRestaurantId(user);
        if (!rid) return noContextError();

        // Build the audience dict the backend expects. The backend
        // OR-joins every non-empty key, so the "one enum + one list"
        // UI on Miya's side maps cleanly to a rich backend filter.
        let audience:
            | "all"
            | { staff_ids?: string[]; roles?: string[]; departments?: string[]; tags?: string[] } = "all";

        const tagsNormalised = (input.tags || [])
            .map((t) => normaliseTag(t))
            .filter((t): t is string => !!t);
        const deptsClean = (input.departments || [])
            .map((d) => (d || "").trim())
            .filter((d) => d.length > 0);

        if (input.audience === "roles" && input.roles?.length) {
            audience = { roles: input.roles };
        } else if (input.audience === "staff_ids" && input.staff_ids?.length) {
            audience = { staff_ids: input.staff_ids };
        } else if (input.audience === "tags" && tagsNormalised.length) {
            audience = { tags: tagsNormalised };
        } else if (input.audience === "departments" && deptsClean.length) {
            audience = { departments: deptsClean };
        }

        // Defensive fallback: when the manager speaks a tag but the
        // LLM forgets to flip `audience` to "tags", still honour the
        // tags list. Same for department. This keeps "Announce to the
        // kitchen: …" working even if `audience` stays as "all".
        if (audience === "all" && (tagsNormalised.length > 0 || deptsClean.length > 0)) {
            audience = {
                ...(tagsNormalised.length > 0 ? { tags: tagsNormalised } : {}),
                ...(deptsClean.length > 0 ? { departments: deptsClean } : {}),
            };
        }

        const result = await this.apiService.sendAnnouncementForAgent(rid, input.message, {
            title: input.title || "Announcement",
            audience,
        });
        if (!result.success) return { status: "error", message: result.error };
        return {
            status: "success",
            message: result.message,
            notification_count: result.notification_count,
        };
    }
}
