import { LuaTool, User, env } from "lua-cli";
import { z } from "zod";
import ApiService, { StaffMember } from "../../services/ApiService";

export default class StaffLookupTool implements LuaTool {
    name = "staff_lookup";
    description = "Retrieve staff member details, list ALL staff, or total count. Use with NO name to get the full list (e.g. 'any available staff', 'look for any staff'). Use countOnly=true for 'how many staff?'. When the user specifies a role (e.g. 'the chef', 'Outmane Jebari (CHEF)'), pass role so similar names are disambiguated. Restaurant ID from context; omit if already in context.";

    inputSchema = z.object({
        name: z.string().optional().describe("Partial or full name of the staff member to search for"),
        role: z.string().optional().describe("Filter staff by role (e.g., WAITER, CHEF)"),
        restaurantId: z.string().optional().describe("Restaurant ID from your [SYSTEM: PERSISTENT CONTEXT] (Restaurant ID: ...). Omit if context already has it."),
        countOnly: z.boolean().optional().describe("When true, return only the total staff count and breakdown (use for 'how many staff?', 'total number of staff')")
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

        // Use type-safe access to user profile/data (widget sends metadata.restaurantId via LuaPop.init)
        const userData = (user as any).data || {};
        const profile = (user as any)._luaProfile || {};
        const metadata = profile.metadata && typeof profile.metadata === 'object' ? profile.metadata : {};

        const restaurantId = input.restaurantId || (user as any).restaurantId || userData.restaurantId
            || profile.restaurantId || profile.restaurant_id
            || (metadata as any).restaurantId || (metadata as any).restaurant_id;
        const token = (user as any).token || userData.token || profile.token || profile.accessToken
            || (metadata as any).token || (metadata as any).accessToken;

        console.log(`[StaffLookupTool] Context debug: restaurantId=${restaurantId || '(none)'}, hasToken=${!!token}`);

        if (!restaurantId && !token) {
            return { status: "error", message: "I don't have your restaurant context right now. Please use Miya from the dashboard while logged in so I can see your staff." };
        }

        try {
            if (input.countOnly) {
                const countResult = await this.apiService.getStaffCountForAgent(restaurantId || "", token);
                return {
                    status: "success",
                    count: countResult.count,
                    by_role: countResult.by_role,
                    message: countResult.message,
                    restaurant_name: countResult.restaurant_name
                };
            }

            console.log(`[StaffLookupTool] Searching staff in restaurant ${restaurantId}...`);
            const staff: StaffMember[] = await this.apiService.getStaffListForAgent(restaurantId || "", input.name, token);

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
                const searchDesc = input.name && input.role
                    ? `the role "${input.role}" named "${input.name}"`
                    : input.role
                        ? `the role "${input.role}"`
                        : `the name "${input.name}"`;

                return {
                    status: "not_found",
                    message: `I couldn't find anyone with ${searchDesc}. \n\nHere are some staff members I found:\n- ${allStaffNames}${staff.length > 10 ? "\n...and others." : ""}`,
                    staff: []
                };
            }

            // User asked for a specific name but multiple matches: ask to clarify (unless role already filtered to one)
            if (filteredStaff.length > 1 && input.name) {
                return {
                    status: "multiple_results",
                    count: filteredStaff.length,
                    message: `I found ${filteredStaff.length} staff with similar names. Use role to narrow (e.g. "the chef"):`,
                    staff: filteredStaff.map(s => ({
                        id: s.id,
                        full_name: `${s.first_name} ${s.last_name}`,
                        role: s.role,
                        position: s.position,
                        department: s.department
                    }))
                };
            }

            // No name = "list all" / "any available staff": return success with full list
            if (filteredStaff.length > 1 && !input.name) {
                return {
                    status: "success",
                    count: filteredStaff.length,
                    message: `Here are all ${filteredStaff.length} staff members. You can schedule any of them by name and role.`,
                    staff: filteredStaff.map(s => ({
                        id: s.id,
                        full_name: `${s.first_name} ${s.last_name}`,
                        role: s.role,
                        position: s.position,
                        department: s.department,
                        email: s.email,
                        phone: s.phone
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
            console.error("[StaffLookupTool] Execution failed:", error?.message);
            const msg = (error?.message || "").toString();
            if (/restaurant context|resolve restaurant|Unable to resolve/i.test(msg)) {
                return { status: "error", message: "I couldn't access your restaurant's staff list right now. Please try again in a moment, or make sure you're logged in through the Mizan dashboard." };
            }
            if (/network|timeout|ECONNREFUSED|fetch/i.test(msg)) {
                return { status: "error", message: "I couldn't reach the server right now. Please check your connection and try again." };
            }
            return { status: "error", message: "I couldn't retrieve staff information. Please try again in a moment." };
        }
    }
}
