import { LuaTool, User, env } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

export default class StaffCommunicationTool implements LuaTool {
    name = "inform_staff";
    description = "Send a WhatsApp message to one or more staff members or specific roles (e.g., 'inform the chef', 'tell the waiters to clean the terrace'). Use this for direct communication and notifications.";

    inputSchema = z.object({
        staff_names: z.array(z.string()).optional().describe("Names of specific staff members to notify (fuzzy match)"),
        role: z.string().optional().describe("Filter staff by role (e.g., 'CHEF', 'WAITER', 'MANAGER') to notify all matching staff"),
        message: z.string().describe("The message to send to the staff"),
        restaurantId: z.string().optional().describe("Restaurant ID from context")
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        // Extract restaurantId from multiple context sources
        const user = await User.get();
        const userData = user ? ((user as any).data || {}) : {};
        const profile = user ? ((user as any)._luaProfile || {}) : {};

        let restaurantId =
            input.restaurantId ||
            (user as any)?.restaurantId ||
            userData.restaurantId ||
            profile.restaurantId;

        if (!restaurantId) {
            return {
                status: "error",
                message: "I need to know which restaurant this is for. Please make sure I have the restaurant context."
            };
        }

        try {
            console.log(`[StaffCommunicationTool] Fetching staff for restaurant ${restaurantId}`);
            const staffList = await this.apiService.getStaffListForAgent(restaurantId);

            let targets = [];

            // Filter by names if provided
            if (input.staff_names && input.staff_names.length > 0) {
                for (const name of input.staff_names) {
                    const matches = await this.apiService.getStaffListForAgent(restaurantId, name);
                    targets.push(...matches);
                }
            }

            // Filter by role if provided
            if (input.role) {
                const searchRole = input.role.toUpperCase();
                const roleMatches = staffList.filter((s: any) =>
                    s.role === searchRole || (s.position && s.position.toUpperCase() === searchRole)
                );
                targets.push(...roleMatches);
            }

            // Deduplicate targets
            const uniqueTargets = Array.from(new Map(targets.map(item => [item.id, item])).values());

            if (uniqueTargets.length === 0) {
                return {
                    status: "error",
                    message: `I couldn't find any staff members matching your request (${input.staff_names ? input.staff_names.join(', ') : ''} ${input.role || ''}).`
                };
            }

            const results = [];
            for (const staff of uniqueTargets) {
                if (staff.phone) {
                    console.log(`[StaffCommunicationTool] Sending message to ${staff.first_name} (${staff.phone})`);
                    const res = await this.apiService.sendWhatsapp({
                        phone: staff.phone,
                        type: 'text',
                        body: input.message
                    }, env('LUA_WEBHOOK_API_KEY') || '');
                    results.push({ name: staff.first_name, success: res.success });
                } else {
                    results.push({ name: staff.first_name, success: false, error: "No phone number" });
                }
            }

            const successCount = results.filter(r => r.success).length;
            const failCount = results.length - successCount;

            return {
                status: "success",
                message: `I've sent the message to ${successCount} staff member(s).${failCount > 0 ? ` Failed for ${failCount}.` : ""}`,
                details: {
                    sent_to: results.filter(r => r.success).map(r => r.name),
                    failed: results.filter(r => !r.success).map(r => r.name)
                }
            };

        } catch (error: any) {
            console.error("[StaffCommunicationTool] Execution failed:", error.message);
            return {
                status: "error",
                message: `Failed to inform staff: ${error.message}`
            };
        }
    }
}
