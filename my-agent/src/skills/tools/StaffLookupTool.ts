import { LuaTool, User, env } from "lua-cli";
import { z } from "zod";
import ApiService, { StaffMember } from "../../services/ApiService";

export default class StaffLookupTool implements LuaTool {
    name = "staff_lookup";
    description = "Retrieve staff member details. ALWAYS extract the restaurant ID from your [SYSTEM: PERSISTENT CONTEXT] block (format: 'ID: xxx') and pass it as restaurantId.";

    inputSchema = z.object({
        name: z.string().optional().describe("Partial or full name of the staff member to search for"),
        role: z.string().optional().describe("Filter staff by role (e.g., WAITER, CHEF)"),
        restaurantId: z.string().describe("REQUIRED: The restaurant ID from your context (e.g., 'aef9c4e0-...')")
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get();
        if (!user) {
            return { status: "error", message: "I can't access your account context right now. Please try again in a moment." };
        }

        // Use type-safe access to user profile/data
        const userData = (user as any).data || {};
        const profile = (user as any)._luaProfile || {};

        const restaurantId = input.restaurantId || (user as any).restaurantId || userData.restaurantId || profile.restaurantId;

        console.log(`[StaffLookupTool] Context debug: restaurantId=${!!restaurantId}`);

        if (!restaurantId) {
            return { status: "error", message: "No restaurant ID found in context." };
        }

        try {
            console.log(`[StaffLookupTool] Searching staff in restaurant ${restaurantId}...`);
            const staff: StaffMember[] = await this.apiService.getStaffListForAgent(restaurantId, input.name);

            if (staff.length === 0) {
                return {
                    status: "not_found",
                    message: `I couldn't find anyone named "${input.name || 'matching your criteria'}" in the staff directory.`,
                    staff: []
                };
            }

            let filteredStaff = [...staff];

            if (input.role) {
                const searchRole = input.role.toUpperCase();
                filteredStaff = filteredStaff.filter(s =>
                    s.role === searchRole || (s.position && s.position.toUpperCase() === searchRole)
                );
            }

            if (filteredStaff.length === 0) {
                const allStaffNames = staff.slice(0, 10).map(s => `${s.first_name} ${s.last_name} (${s.role})`).join("\n- ");
                return {
                    status: "not_found",
                    message: `I couldn't find anyone with the role "${input.role}" named "${input.name}". \n\nHere are some staff members I found:\n- ${allStaffNames}${staff.length > 10 ? "\n...and others." : ""}`,
                    staff: []
                };
            }

            if (filteredStaff.length > 1) {
                return {
                    status: "multiple_results",
                    count: filteredStaff.length,
                    message: `I found ${filteredStaff.length} matching staff members. Please clarify which one:`,
                    staff: filteredStaff.map(s => ({
                        id: s.id,
                        full_name: `${s.first_name} ${s.last_name}`,
                        role: s.role,
                        position: s.position,
                        department: s.department
                    }))
                };
            }

            return {
                status: "success",
                count: filteredStaff.length,
                staff: filteredStaff.map(s => ({
                    id: s.id,
                    full_name: `${s.first_name} ${s.last_name}`,
                    role: s.role,
                    position: s.position,
                    department: s.department,
                    skills: s.skills || []
                })),
                message: `Found ${filteredStaff[0].first_name} ${filteredStaff[0].last_name}.`
            };
        } catch (error: any) {
            console.error("[StaffLookupTool] Execution failed:", error.message);
            return {
                status: "error",
                message: `Failed to retrieve staff profiles: ${error.message}`
            };
        }
    }
}
