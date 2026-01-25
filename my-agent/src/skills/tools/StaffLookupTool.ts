import { LuaTool } from "lua-cli";
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

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        const restaurantId =
            input.restaurantId ||
            (context?.get ? context.get("restaurantId") : undefined) ||
            context?.user?.data?.restaurantId ||
            context?.metadata?.restaurantId;

        const token =
            context?.metadata?.token ||
            (context?.get ? context.get("token") : undefined) ||
            context?.user?.data?.token ||
            context?.user?.token ||
            process.env.MIZAN_SERVICE_TOKEN; // Fallback to service token

        console.log(`[StaffLookupTool] Context debug: restaurantId=${!!restaurantId}, token=${!!token}, source=${token ? (context?.metadata?.token ? 'metadata' : context?.user?.data?.token ? 'user.data' : 'env') : 'none'}`);

        if (!restaurantId) {
            return { status: "error", message: "No restaurant ID found in context. Make sure you're logged into a restaurant." };
        }
        if (!token) {
            return { status: "error", message: "No authentication token found. Please log in again or contact support." };
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
