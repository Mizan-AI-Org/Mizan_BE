import { LuaTool, User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

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
        const userData = (user as any).data || {};
        const profile = (user as any)._luaProfile || {};

        const restaurantId =
            input.restaurantId ||
            user.restaurantId ||
            userData.restaurantId ||
            profile.restaurantId;

        const token =
            user.token ||
            userData.token ||
            profile.token ||
            profile.accessToken ||
            profile.credentials?.accessToken ||
            process.env.MIZAN_SERVICE_TOKEN; // Fallback to service token

        console.log(`[StaffLookupTool] V7 Context debug: restaurantId=${!!restaurantId}, token=${!!token}`);

        if (!restaurantId) {
            return { status: "error", message: "[V7 DIAGNOSTIC] No restaurant ID found in context. (Keys: " + Object.keys(userData).join(',') + ")" };
        }
        if (!token) {
            return { status: "error", message: "[V7 DIAGNOSTIC] No authentication token found. (Keys: " + Object.keys(userData).join(',') + ")" };
        }

        try {
            console.log(`[StaffLookupTool] Searching staff in restaurant ${restaurantId}...`);
            const staff = await this.apiService.getStaffProfiles(restaurantId, token);

            let filteredStaff = staff;

            if (input.name) {
                const searchName = input.name.toLowerCase();
                filteredStaff = filteredStaff.filter((s: any) =>
                    `${s.first_name} ${s.last_name}`.toLowerCase().includes(searchName) ||
                    s.first_name.toLowerCase().includes(searchName) ||
                    s.last_name.toLowerCase().includes(searchName)
                );
            }

            if (input.role) {
                const searchRole = input.role.toUpperCase();
                filteredStaff = filteredStaff.filter((s: any) =>
                    s.role === searchRole || (s.position && s.position.toUpperCase() === searchRole)
                );
            }

            if (filteredStaff.length > 1) {
                return {
                    status: "multiple_results",
                    count: filteredStaff.length,
                    message: `I found ${filteredStaff.length} matching staff members. Please clarify which one:`,
                    staff: filteredStaff.map((s: any) => ({
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
                staff: filteredStaff.map((s: any) => ({
                    id: s.id,
                    full_name: `${s.first_name} ${s.last_name}`,
                    role: s.role,
                    position: s.position,
                    department: s.department,
                    skills: s.skills || []
                })),
                message: filteredStaff.length > 0 ? `Found ${filteredStaff.length} matching staff members.` : "No matching staff members found."
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
