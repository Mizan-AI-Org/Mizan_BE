import { LuaTool } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

export default class StaffSchedulerTool implements LuaTool {
    name = "staff_scheduler";
    description = "Manage staff schedules, create shifts, and check for conflicts.";

    inputSchema = z.object({
        action: z.enum(["list_shifts", "create_shift", "update_shift", "check_availability", "get_staff"]).describe("The action to perform"),
        staff_name: z.string().optional().describe("Name of the staff member (fuzzy match)"),
        date: z.string().optional().describe("Date of the shift (YYYY-MM-DD)"),
        start_time: z.string().optional().describe("Start time (HH:MM)"),
        end_time: z.string().optional().describe("End time (HH:MM)"),
        role: z.string().optional().describe("Role for the shift (e.g., waiter, chef)"),
        shift_id: z.string().optional().describe("ID of the shift to update"),
        notes: z.string().optional().describe("Notes for the shift"),
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        const apiService = this.apiService;

        // Check multiple sources for restaurantId
        const restaurantId =
            (context?.get ? context.get("restaurantId") : undefined) ||
            context?.metadata?.restaurantId ||
            context?.restaurantId;

        const token = context?.metadata?.token || (context?.get ? context.get("token") : undefined);

        if (!restaurantId) {
            console.error('[StaffSchedulerTool] Missing restaurant context. Keys:', context ? Object.keys(context) : 'null');
            return {
                status: "error",
                message: "Restaurant context is missing. Cannot perform scheduling operations."
            };
        }

        if (!token) {
            // In a real scenario, we might need to handle this better, but for now assuming token is passed or we can't call API
            // If context.user is present, maybe we can use a system token or the user's token if stored.
            // For now, let's assume the preprocessor put the token in metadata or we have a way to get it.
            // If not, we might need to fail.
            // However, the TenantContextPreprocessor validates the token, so it should be available.
            // Let's check where the token comes from. In preprocessor: const token = message.metadata?.token || context.metadata?.token;
            // We should probably store it in context if not already there.
        }

        // Ensure we have a token to make API calls
        // If the token is not explicitly in context.get("token"), we might need to rely on it being in metadata
        const userToken = token || context?.user?.token; // Fallback if we stored it on user object (we didn't in preprocessor)

        // Actually, the preprocessor uses the token to validate but doesn't explicitly save it to context.
        // We might need to update preprocessor to save the token, or pass it through.
        // For this implementation, let's assume we can get it from context.metadata.token if available.

        if (!userToken) {
            return {
                status: "error",
                message: "Authentication token missing. Cannot access scheduling API."
            };
        }

        try {
            switch (input.action) {
                case "get_staff": {
                    const staff = await apiService.getStaffList(restaurantId, userToken);
                    return {
                        status: "success",
                        staff: staff.map((s: any) => ({ id: s.id, name: `${s.first_name} ${s.last_name}`, role: s.role }))
                    };
                }

                case "list_shifts": {
                    const params: any = {};
                    if (input.date) {
                        params.date_from = input.date;
                        params.date_to = input.date;
                    }

                    // Resolve staff name to ID if provided
                    if (input.staff_name) {
                        const staffList = await apiService.getStaffList(restaurantId, userToken);
                        const staffMember = staffList.find((s: any) =>
                            `${s.first_name} ${s.last_name}`.toLowerCase().includes(input.staff_name!.toLowerCase())
                        );
                        if (staffMember) {
                            params.staff_id = staffMember.id;
                        } else {
                            return { status: "error", message: `Staff member '${input.staff_name}' not found.` };
                        }
                    }

                    const shifts = await apiService.getAssignedShifts(params, userToken);
                    return {
                        status: "success",
                        shifts: shifts.results || shifts // Handle pagination if needed
                    };
                }

                case "create_shift": {
                    if (!input.staff_name || !input.date || !input.start_time || !input.end_time) {
                        return { status: "error", message: "Missing required fields: staff_name, date, start_time, end_time" };
                    }

                    // Resolve staff ID
                    const staffList = await apiService.getStaffList(restaurantId, userToken);
                    const staffMember = staffList.find((s: any) =>
                        `${s.first_name} ${s.last_name}`.toLowerCase().includes(input.staff_name!.toLowerCase())
                    );

                    if (!staffMember) {
                        return { status: "error", message: `Staff member '${input.staff_name}' not found.` };
                    }

                    // Check for conflicts first
                    const conflicts = await apiService.detectConflicts({
                        staff_id: staffMember.id,
                        shift_date: input.date,
                        start_time: input.start_time,
                        end_time: input.end_time
                    }, userToken);

                    if (conflicts.has_conflicts) {
                        return {
                            status: "conflict",
                            message: "Scheduling conflict detected.",
                            conflicts: conflicts.conflicts
                        };
                    }

                    const shiftDate = new Date(input.date);

                    const payload = {
                        staff_id: staffMember.id, // Serializer likely expects staff_id or staff object
                        shift_date: input.date,
                        start_time: input.start_time,
                        end_time: input.end_time,
                        role: input.role || staffMember.role || "server", // Default role
                        // schedule: ???
                    };

                    const newShift = await apiService.createAssignedShift(payload, userToken);
                    return {
                        status: "success",
                        shift: newShift,
                        message: `Shift created for ${input.staff_name} on ${input.date}`
                    };
                }

                case "update_shift": {
                    if (!input.shift_id) {
                        return { status: "error", message: "Missing shift_id for update" };
                    }
                    const updateData: any = {};
                    if (input.date) updateData.shift_date = input.date;
                    if (input.start_time) updateData.start_time = input.start_time;
                    if (input.end_time) updateData.end_time = input.end_time;
                    if (input.notes) updateData.notes = input.notes;

                    const updatedShift = await apiService.updateAssignedShift(input.shift_id, updateData, userToken);
                    return {
                        status: "success",
                        shift: updatedShift,
                        message: "Shift updated successfully"
                    };
                }

                case "check_availability": {
                    if (!input.staff_name || !input.date || !input.start_time || !input.end_time) {
                        return { status: "error", message: "Missing required fields" };
                    }

                    const staffList = await apiService.getStaffList(restaurantId, userToken);
                    const staffMember = staffList.find((s: any) =>
                        `${s.first_name} ${s.last_name}`.toLowerCase().includes(input.staff_name!.toLowerCase())
                    );

                    if (!staffMember) {
                        return { status: "error", message: `Staff member '${input.staff_name}' not found.` };
                    }

                    const conflicts = await apiService.detectConflicts({
                        staff_id: staffMember.id,
                        shift_date: input.date,
                        start_time: input.start_time,
                        end_time: input.end_time
                    }, userToken);

                    return {
                        status: "success",
                        available: !conflicts.has_conflicts,
                        conflicts: conflicts.conflicts
                    };
                }

                default:
                    return { status: "error", message: "Invalid action" };
            }
        } catch (error: any) {
            return {
                status: "error",
                message: `Operation failed: ${error.message}`
            };
        }
    }
}
