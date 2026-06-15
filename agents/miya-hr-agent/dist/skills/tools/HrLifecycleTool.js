/**
 * HrLifecycleTool — list staff roster + perform HR actions via Miya.
 * Actions:
 *   - list: show roster (active/inactive/all, optionally filtered by role)
 *   - offboard: disable a staff member's account (is_active=False)
 *   - reactivate: re-enable a previously disabled account
 *   - transfer: change a staff member's role within the same workspace
 *
 * Use for: "show inactive staff", "offboard X", "Y left the company",
 * "move Z to manager", "promote X", "reactivate Y's account".
 *
 * HIRING / INVITING new staff still goes through the existing invite flow —
 * Miya should use InviteStaff endpoints (or ask manager to send invite from dashboard).
 */
import { User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { noContextError, upstreamError, validationError } from "./_common/errors";
export default class HrLifecycleTool {
    constructor(apiService = new ApiService()) {
        this.apiService = apiService;
        this.name = "hr_lifecycle";
        this.description = "HR lifecycle operations. action='list' returns the workspace roster (active/inactive/all). " +
            "action='offboard' disables a staff account (they can no longer log in). " +
            "action='reactivate' re-enables a previously offboarded account. " +
            "action='transfer' changes a staff member's role on the same workspace (requires new_role). " +
            "To HIRE/INVITE a new staff member, instruct the manager to use the dashboard invite flow or existing invite_staff workflow.";
        this.inputSchema = z.object({
            action: z.enum(["list", "offboard", "reactivate", "transfer"]),
            status: z.enum(["active", "inactive", "all"]).optional().describe("For 'list'. Default 'active'."),
            role: z.string().optional().describe("For 'list': role filter."),
            staff_id: z.string().optional(),
            phone: z.string().optional(),
            new_role: z.string().optional().describe("Required for action='transfer'."),
            reason: z.string().optional().describe("Optional reason for the HR action (audited in notes)."),
            limit: z.number().min(1).max(200).optional(),
            restaurantId: z.string().optional().describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]. Do NOT omit."),
        });
    }
    async execute(input) {
        const user = await User.get();
        if (!user)
            return noContextError();
        const userData = user.data || {};
        const profile = user._luaProfile || {};
        const rid = input.restaurantId ||
            user.restaurantId ||
            userData.restaurantId ||
            profile.restaurantId;
        if (!rid)
            return noContextError();
        if (input.action === "list") {
            const res = await this.apiService.hrLifecycleListForAgent(rid, {
                status: input.status,
                role: input.role,
                limit: input.limit,
            });
            if (res && res.success === false)
                return upstreamError(res.error);
            return {
                status: "success",
                count: res?.count || 0,
                staff: res?.staff || [],
                message: (res?.count || 0) === 0
                    ? "No staff match that filter."
                    : `${res.count} staff (${input.status || "active"}).`,
                miya_directive: "Summarise in the user's language. Show names + roles + active/inactive compactly.",
            };
        }
        if (input.action === "transfer" && !input.new_role) {
            return validationError("new_role is required for action='transfer'.");
        }
        if (!input.staff_id && !input.phone) {
            return validationError("Provide staff_id or phone for this HR action.");
        }
        const res = await this.apiService.hrLifecycleActionForAgent({
            restaurant_id: rid,
            action: input.action,
            staff_id: input.staff_id,
            phone: input.phone,
            new_role: input.new_role,
            reason: input.reason,
        });
        if (res && res.success === false)
            return upstreamError(res.error);
        return {
            status: "success",
            staff_id: res?.staff_id,
            message: res?.message || "HR action completed.",
            miya_directive: "Confirm briefly in the user's language.",
        };
    }
}
