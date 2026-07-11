import { LuaTool, User, env } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import {
    classifyStaffEscalation,
    isMisroutedStaffEscalation,
    type StaffRouteKind,
} from "../../utils/staffEscalationRouting";
import {
    resolveStaffPhoneForByPhoneTools,
    type LuaUserPhoneSource,
} from "../../utils/resolveStaffPhoneFromLuaUser";

/**
 * inform_staff — direct WhatsApp message to one or more staff members.
 *
 * Targeting options (union — all matches are OR-joined + deduped):
 *   - staff_names:  fuzzy match by name ("Adam", "Salima Majdallah")
 *   - role:         formal job title (CHEF, WAITER, MANAGER, …)
 *   - tags:         canonical operational tags — KITCHEN, SERVICE,
 *                   FRONT_OFFICE, BACK_OFFICE, PURCHASES, CONTROL,
 *                   ADMINISTRATION, MANAGEMENT, HOUSEKEEPING, MARKETING.
 *                   Use these for "tell the kitchen", "message all
 *                   service staff", "let housekeeping know".
 *   - department:   free-text StaffProfile.department (case-insensitive).
 *
 * Phone format requirement: the restaurant stores numbers as country
 * code + subscriber number, digits only, no '+' and no leading zero
 * (e.g. 212784476751 = Morocco, 2203736808 = Gambia). The backend
 * normalises common variants (+, spaces, dashes, 00-prefix), but if a
 * staff member's phone is saved in a bad format the send will fail with
 * a specific error the manager can act on.
 */
export default class StaffCommunicationTool implements LuaTool {
    name = "inform_staff";
    description =
        "Send a WhatsApp message directly to one or more staff members — by name, by role, by canonical tag (KITCHEN, SERVICE, FRONT_OFFICE, BACK_OFFICE, PURCHASES, CONTROL, ADMINISTRATION, MANAGEMENT, HOUSEKEEPING, MARKETING), or by department. Use this for 'tell Adam to come in', 'message the kitchen', 'let housekeeping know we're closing early', 'inform all service staff'. For a formal in-app + WhatsApp announcement use send_announcement instead.";

    inputSchema = z.object({
        staff_names: z
            .array(z.string())
            .optional()
            .describe("Names of specific staff members to notify (fuzzy match)"),
        role: z
            .string()
            .optional()
            .describe("Formal job title to target (e.g., 'CHEF', 'WAITER', 'MANAGER')"),
        tags: z
            .array(z.string())
            .optional()
            .describe(
                "Canonical operational tags to target a crew. Any-of match. Allowed: KITCHEN, SERVICE, FRONT_OFFICE, BACK_OFFICE, PURCHASES, CONTROL, ADMINISTRATION, MANAGEMENT, HOUSEKEEPING, MARKETING.",
            ),
        department: z
            .array(z.string())
            .optional()
            .describe("StaffProfile.department string(s) to target (case-insensitive exact match)"),
        message: z.string().describe("The message to send to the staff"),
        restaurantId: z.string().optional().describe("Restaurant ID from context"),
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    /**
     * Normalise a tag-looking string to the canonical UPPER_SNAKE form.
     * Mirrors `accounts.staff_tags.normalize_tag` so what Miya sends
     * matches what the backend expects.
     */
    private normalizeTag(value: string | null | undefined): string | null {
        if (!value) return null;
        const cleaned = String(value).trim().toUpperCase().replace(/[-\s]+/g, "_").replace(/_+/g, "_");
        return cleaned || null;
    }

    private phoneFromUser(user: unknown): string {
        const u = user as LuaUserPhoneSource & { uid?: string };
        return resolveStaffPhoneForByPhoneTools(
            {
                uid: u?.uid,
                data: (u as { data?: Record<string, unknown> })?.data,
                _luaProfile: (u as { _luaProfile?: Record<string, unknown> })?._luaProfile,
            },
            null,
        );
    }

    private async redirectStaffEscalationToRequest(
        routed: { category: StaffRouteKind; subject: string },
        description: string,
        restaurantId: string,
        phone: string,
        channel?: string,
    ) {
        console.log(
            `[StaffCommunicationTool] Guard: redirecting staff escalation to staff_request category=${routed.category}`,
        );
        const result = await this.apiService.createStaffRequestForAgent({
            restaurant_id: restaurantId,
            subject: routed.subject,
            description,
            category: routed.category,
            priority: routed.category === "MAINTENANCE" ? "HIGH" : "MEDIUM",
            phone: phone || undefined,
            auto_assign: true,
            metadata: {
                source_context: "inform_staff_guard",
                channel: channel || "whatsapp",
            },
        });

        if (!result.success) {
            return {
                status: "error",
                message:
                    "I couldn't pass that to your manager just now. Please try again in a moment.",
            };
        }

        const apiMsg =
            typeof (result as { message_for_staff?: string }).message_for_staff === "string"
                ? String((result as { message_for_staff?: string }).message_for_staff).trim()
                : "";

        const fallback =
            routed.category === "PAYROLL"
                ? "Thanks — I've passed your payroll note on to your manager. They'll get back to you as soon as they can."
                : "Thanks — I've passed that on to your manager. They'll get back to you as soon as they can.";

        return {
            status: "success",
            message: apiMsg || fallback,
            details: { record_id: result.id, category: routed.category, redirected_from: "inform_staff" },
        };
    }

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        const user = await User.get();
        const userData = user ? ((user as any).data || {}) : {};
        const profile = user ? ((user as any)._luaProfile || {}) : {};

        const restaurantId =
            input.restaurantId ||
            (user as any)?.restaurantId ||
            userData.restaurantId ||
            profile.restaurantId;

        if (!restaurantId) {
            return {
                status: "error",
                message: "I need to know which restaurant this is for. Please make sure I have the restaurant context.",
            };
        }

        const channel =
            typeof context?.channel === "string"
                ? context.channel
                : typeof context?.channel === "object"
                  ? String(context.channel?.type || context.channel?.name || "")
                  : "";

        if (
            isMisroutedStaffEscalation(input.message, input.staff_names) ||
            classifyStaffEscalation(input.message)
        ) {
            const routed = classifyStaffEscalation(input.message) || {
                category: "PAYROLL" as StaffRouteKind,
                subject: input.message.slice(0, 200),
            };
            const phone = this.phoneFromUser(user);
            return this.redirectStaffEscalationToRequest(
                routed,
                input.message.trim(),
                restaurantId,
                phone,
                channel,
            );
        }

        const tags = (input.tags || [])
            .map((t) => this.normalizeTag(t))
            .filter((t): t is string => !!t);
        const departments = (input.department || [])
            .map((d) => (d || "").trim())
            .filter((d) => d.length > 0);

        try {
            console.log(
                `[StaffCommunicationTool] Fetching staff for restaurant ${restaurantId} (names=${(input.staff_names || []).length}, role=${input.role || "-"}, tags=${tags.join("|") || "-"}, dept=${departments.join("|") || "-"})`,
            );

            const targets: any[] = [];

            // Name-based lookup — each name is its own fuzzy call so
            // partial matches ("Adam") still work.
            if (input.staff_names && input.staff_names.length > 0) {
                for (const name of input.staff_names) {
                    const matches = await this.apiService.getStaffListForAgent(restaurantId, name);
                    targets.push(...matches);
                }
            }

            // Role-based: fetch the full list once, filter locally on
            // role / position. (The backend's role filter is covered by
            // the tags/dept path below when we use it; keeping this as
            // a dedicated branch preserves the legacy shape.)
            if (input.role) {
                const staffList = await this.apiService.getStaffListForAgent(restaurantId);
                const searchRole = input.role.toUpperCase();
                const roleMatches = staffList.filter(
                    (s: any) => s.role === searchRole || (s.position && s.position.toUpperCase() === searchRole),
                );
                targets.push(...roleMatches);
            }

            // Tag and/or department — single server call with filters.
            // Any-of semantics across tags + case-insensitive on dept
            // match what send_announcement_to_audience does on the
            // backend, so both tools stay consistent.
            if (tags.length > 0 || departments.length > 0) {
                const filtered = await this.apiService.getStaffListForAgent(
                    restaurantId,
                    undefined,
                    undefined,
                    undefined,
                    {
                        tags: tags.length > 0 ? tags : undefined,
                        department: departments.length > 0 ? departments : undefined,
                    },
                );
                targets.push(...filtered);
            }

            // If no filter was provided at all this tool is ambiguous —
            // refuse rather than spam every staff member. The manager
            // should either use send_announcement (audience: all) or
            // name a subset.
            if (
                (!input.staff_names || input.staff_names.length === 0) &&
                !input.role &&
                tags.length === 0 &&
                departments.length === 0
            ) {
                return {
                    status: "error",
                    message:
                        "Tell me who to message — a name, a role, a tag (KITCHEN, SERVICE, HOUSEKEEPING, …), or a department. For an all-staff blast use send_announcement instead.",
                };
            }

            // Deduplicate by user id so a chef who is both a CHEF and
            // carries the KITCHEN tag only gets one message.
            const uniqueTargets = Array.from(new Map(targets.map((item) => [item.id, item])).values());

            if (uniqueTargets.length === 0) {
                const probe = [
                    input.staff_names ? `names=${(input.staff_names || []).join(", ")}` : null,
                    input.role ? `role=${input.role}` : null,
                    tags.length > 0 ? `tags=${tags.join(", ")}` : null,
                    departments.length > 0 ? `department=${departments.join(", ")}` : null,
                ]
                    .filter(Boolean)
                    .join("; ");
                return {
                    status: "error",
                    message: `I couldn't find any staff matching: ${probe}. Check the name spelling, or the tag/department — tags use the canonical list (KITCHEN, SERVICE, FRONT_OFFICE, BACK_OFFICE, PURCHASES, CONTROL, ADMINISTRATION, MANAGEMENT, HOUSEKEEPING, MARKETING).`,
                };
            }

            const results: Array<{ name: string; success: boolean; error?: string }> = [];
            for (const staff of uniqueTargets) {
                const displayName = staff.first_name || staff.full_name || "staff member";
                if (!staff.phone) {
                    results.push({ name: displayName, success: false, error: "No WhatsApp number on file" });
                    continue;
                }
                console.log(`[StaffCommunicationTool] Sending message to ${displayName} (${staff.phone})`);
                const res = await this.apiService.sendWhatsapp(
                    { phone: staff.phone, type: "text", body: input.message },
                    env("LUA_WEBHOOK_API_KEY") || "",
                );
                results.push({
                    name: displayName,
                    success: !!res.success,
                    error: res.success ? undefined : (res as any)?.error || "WhatsApp delivery failed",
                });
            }

            const sent = results.filter((r) => r.success);
            const failed = results.filter((r) => !r.success);

            const descriptor =
                tags.length > 0 && !input.staff_names && !input.role && departments.length === 0
                    ? `${tags.join(", ").toLowerCase().replace(/_/g, " ")} team`
                    : departments.length > 0 && !input.staff_names && !input.role && tags.length === 0
                        ? `${departments.join(", ")} department`
                        : `${sent.length} staff member${sent.length === 1 ? "" : "s"}`;

            let summary = sent.length > 0
                ? `I've sent the WhatsApp message to ${sent.length} person${sent.length === 1 ? "" : "s"}${sent.length > 0 ? ` (${sent.map((r) => r.name).join(", ")})` : ""}.`
                : `I couldn't deliver the message to anyone in the ${descriptor}.`;

            if (failed.length > 0) {
                summary += ` Delivery failed for ${failed.length}: ${failed.map((r) => `${r.name}${r.error ? ` (${r.error})` : ""}`).join(", ")}. If it's a phone-format issue, the number must be country code + subscriber digits only, no '+' and no leading zero (e.g. 212784476751 or 2203736808).`;
            }

            return {
                status: sent.length > 0 ? "success" : "error",
                message: summary,
                details: {
                    sent_to: sent.map((r) => r.name),
                    failed: failed.map((r) => ({ name: r.name, error: r.error || null })),
                    targeted_count: uniqueTargets.length,
                    filter: { tags, departments, role: input.role || null, names: input.staff_names || [] },
                },
            };
        } catch (error: any) {
            console.error("[StaffCommunicationTool] Execution failed:", error?.message || error);
            return {
                status: "error",
                message: `Failed to inform staff: ${error?.message || "Unknown error"}`,
            };
        }
    }
}
