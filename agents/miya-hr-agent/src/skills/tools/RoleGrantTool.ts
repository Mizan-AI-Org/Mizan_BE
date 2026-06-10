/**
 * RoleGrantTool — change/assign a staff member's role in the workspace.
 * The actual permission set for each role is managed via the RBAC dashboard
 * (rbac/role-permissions/). This tool just updates the role tag on the user.
 */
import { LuaTool, User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { noContextError, upstreamError, validationError } from "./_common/errors";

export default class RoleGrantTool implements LuaTool {
    name = "grant_role";
    description =
        "Grant / change a staff member's role on this workspace (e.g. 'make X a manager', 'promote Y to supervisor'). " +
        "Permissions for each role are controlled via the RBAC dashboard, not here. " +
        "Requires staff_id or phone and a role string.";

    inputSchema = z.object({
        role: z.string().describe("Role name (e.g. 'MANAGER', 'SUPERVISOR', 'WAITER', 'COOK', 'CASHIER', etc.)."),
        staff_id: z.string().optional(),
        phone: z.string().optional(),
        restaurantId: z.string().optional().describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]. Do NOT omit."),
    });

    constructor(private apiService: ApiService = new ApiService()) {}

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get();
        if (!user) return noContextError();
        const userData = (user as any).data || {};
        const profile = (user as any)._luaProfile || {};
        const rid: string | undefined =
            input.restaurantId ||
            (user as any).restaurantId ||
            userData.restaurantId ||
            profile.restaurantId;
        if (!rid) return noContextError();
        if (!input.role || !input.role.trim()) return validationError("role is required.");
        if (!input.staff_id && !input.phone) return validationError("Provide staff_id or phone.");

        const res = await this.apiService.grantRoleForAgent({
            restaurant_id: rid,
            role: input.role.trim().toUpperCase(),
            staff_id: input.staff_id,
            phone: input.phone,
        });
        if (res && res.success === false) return upstreamError(res.error);
        return {
            status: "success",
            staff_id: res?.staff_id,
            role: res?.role,
            message: res?.message || "Role updated.",
            miya_directive:
                "Confirm in the user's language. Mention that permissions for that role are controlled via the RBAC dashboard if further tuning is needed.",
        };
    }
}
