import { LuaTool, User, env } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

export default class StaffSchedulerTool implements LuaTool {
    name = "staff_scheduler";
    description = "Schedule staff shifts. ALWAYS extract the restaurant ID from your [SYSTEM: PERSISTENT CONTEXT] block and pass it as restaurantId. For time periods like 'lunch', use 12:00-15:00; 'dinner' use 19:00-23:00.";

    inputSchema = z.object({
        action: z.enum(["list_shifts", "create_shift", "update_shift", "check_availability", "get_staff"]).describe("The action to perform"),
        staff_name: z.string().optional().describe("Name of the staff member (fuzzy match)"),
        date: z.string().optional().describe("Date of the shift (YYYY-MM-DD). For 'tomorrow', add 1 day to today's date from your context."),
        start_time: z.string().optional().describe("Start time (HH:MM). For 'lunch' use 12:00, for 'dinner' use 19:00."),
        end_time: z.string().optional().describe("End time (HH:MM). For 'lunch' use 15:00, for 'dinner' use 23:00."),
        role: z.string().optional().describe("Role for the shift - leave empty to use staff member's existing role"),
        shift_id: z.string().optional().describe("ID of the shift to update"),
        notes: z.string().optional().describe("Notes for the shift"),
        restaurantId: z.string().describe("REQUIRED: The restaurant ID from your context (e.g., 'aef9c4e0-...')"),
        is_recurring: z.boolean().optional().describe("Whether this shift should be recurring"),
        frequency: z.enum(["DAILY", "WEEKLY"]).optional().describe("Recurrence frequency if is_recurring is true"),
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    async execute(input: z.infer<typeof this.inputSchema>) {
        const apiService = this.apiService;

        const user = await User.get();
        if (!user) {
            return {
                status: "error",
                message: "I can't access your account context right now. Please try again in a moment."
            };
        }
        const userData = (user as any).data || {};
        const profile = (user as any)._luaProfile || {};

        // Check multiple sources for restaurantId
        const restaurantId =
            input.restaurantId ||
            user.restaurantId ||
            userData.restaurantId ||
            profile.restaurantId;

        // Token retrieval with service account fallback
        const token =
            user.token ||
            userData.token ||
            profile.token ||
            profile.accessToken ||
            profile.credentials?.accessToken ||
            env('MIZAN_SERVICE_TOKEN'); // Service account fallback

        console.log(`[StaffSchedulerTool] V7 Context debug: restaurantId=${!!restaurantId}, token=${!!token}`);

        if (!restaurantId) {
            console.error('[StaffSchedulerTool] Missing restaurant context.');
            return {
                status: "error",
                message: "[V7 DIAGNOSTIC] Restaurant ID is missing. (Keys: " + Object.keys(userData).join(',') + ") I don't have your restaurant context. Please make sure you're logged in through the Mizan app."
            };
        }

        if (!token) {
            console.error('[StaffSchedulerTool] No authentication token available.');
            return {
                status: "error",
                message: "I can't access the scheduling system right now. Please try again or contact support."
            };
        }

        console.log(`[StaffSchedulerTool] Executing ${input.action} for restaurant ${restaurantId}`);

        try {
            switch (input.action) {
                case "get_staff": {
                    const staff = await apiService.getStaffList(restaurantId, token);
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

                    if (input.staff_name) {
                        const staffList = await apiService.getStaffList(restaurantId, token);
                        const staffMember = staffList.find((s: any) =>
                            `${s.first_name} ${s.last_name}`.toLowerCase().includes(input.staff_name!.toLowerCase())
                        );
                        if (staffMember) {
                            params.staff_id = staffMember.id;
                        } else {
                            return { status: "error", message: `Staff member '${input.staff_name}' not found.` };
                        }
                    }

                    const shifts = await apiService.getAssignedShifts(params, token);
                    return {
                        status: "success",
                        shifts: shifts.results || shifts
                    };
                }

                case "create_shift": {
                    if (!input.staff_name || !input.date || !input.start_time || !input.end_time) {
                        return { status: "error", message: "Missing required fields: staff_name, date, start_time, end_time" };
                    }

                    const staffList = await apiService.getStaffList(restaurantId, token);
                    const matches = staffList.filter((s: any) =>
                        `${s.first_name} ${s.last_name}`.toLowerCase().includes(input.staff_name!.toLowerCase())
                    );

                    if (matches.length === 0) {
                        return { status: "error", message: `Staff member '${input.staff_name}' not found.` };
                    }

                    if (matches.length > 1) {
                        return {
                            status: "multiple_results",
                            message: `I found ${matches.length} staff members matching '${input.staff_name}'.`,
                            matches: matches.map((s: any) => ({ id: s.id, name: `${s.first_name} ${s.last_name}`, role: s.role }))
                        };
                    }

                    const staffMember = matches[0];
                    const finalRole = input.role || staffMember.role || "server";

                    // Handle Recurrence
                    const datesToSchedule = [input.date];
                    if (input.is_recurring && input.frequency) {
                        const baseDate = new Date(input.date);
                        for (let i = 1; i <= 3; i++) { // Schedule 3 more occurrences (4 total)
                            const nextDate = new Date(baseDate);
                            if (input.frequency === "DAILY") {
                                nextDate.setDate(baseDate.getDate() + i);
                            } else if (input.frequency === "WEEKLY") {
                                nextDate.setDate(baseDate.getDate() + (i * 7));
                            }
                            datesToSchedule.push(nextDate.toISOString().split('T')[0]);
                        }
                    }

                    const results = [];
                    for (const shiftDate of datesToSchedule) {
                        // Check conflicts for each date
                        const conflicts = await apiService.detectConflicts({
                            staff_id: staffMember.id,
                            shift_date: shiftDate,
                            start_time: input.start_time,
                            end_time: input.end_time
                        }, token);

                        if (conflicts.has_conflicts) {
                            results.push({ date: shiftDate, status: "conflict", message: "Shift overlaps with existing schedule" });
                            continue;
                        }

                        const payload = {
                            staff_id: staffMember.id,
                            shift_date: shiftDate,
                            start_time: input.start_time,
                            end_time: input.end_time,
                            role: finalRole,
                            notes: input.notes,
                            restaurant_id: restaurantId
                        };

                        try {
                            const newShift = await apiService.createAssignedShift(payload, token);
                            results.push({ date: shiftDate, status: "success", shift: newShift });
                        } catch (err: any) {
                            results.push({ date: shiftDate, status: "error", message: err.message });
                        }
                    }

                    const successCount = results.filter(r => r.status === "success").length;
                    const errorMsgs = results.filter(r => r.status !== "success").map(r => `${r.date}: ${r.message}`);

                    if (successCount === 0) {
                        return { status: "error", message: `Failed to create shifts. Reasons: ${errorMsgs.join(", ")}` };
                    }

                    return {
                        status: "success",
                        message: `Successfully scheduled ${successCount} shift(s) for ${staffMember.first_name}.` +
                            (errorMsgs.length > 0 ? ` Some dates failed: ${errorMsgs.join(", ")}` : ""),
                        data: results
                    };
                }

                case "update_shift": {
                    if (!input.shift_id) return { status: "error", message: "Missing shift_id" };
                    const updateData: any = {};
                    if (input.date) updateData.shift_date = input.date;
                    if (input.start_time) updateData.start_time = input.start_time;
                    if (input.end_time) updateData.end_time = input.end_time;
                    if (input.notes) updateData.notes = input.notes;

                    const updatedShift = await apiService.updateAssignedShift(input.shift_id, updateData, token);
                    return { status: "success", shift: updatedShift, message: "Shift updated successfully" };
                }

                case "check_availability": {
                    if (!input.staff_name || !input.date || !input.start_time || !input.end_time) {
                        return { status: "error", message: "Missing required fields" };
                    }
                    const staffList = await apiService.getStaffList(restaurantId, token);
                    const staffMember = staffList.find((s: any) =>
                        `${s.first_name} ${s.last_name}`.toLowerCase().includes(input.staff_name!.toLowerCase())
                    );
                    if (!staffMember) return { status: "error", message: `Staff member '${input.staff_name}' not found.` };

                    const conflicts = await apiService.detectConflicts({
                        staff_id: staffMember.id,
                        shift_date: input.date,
                        start_time: input.start_time,
                        end_time: input.end_time
                    }, token);

                    return { status: "success", available: !conflicts.has_conflicts, conflicts: conflicts.conflicts };
                }

                default:
                    return { status: "error", message: "Invalid action" };
            }
        } catch (error: any) {
            return { status: "error", message: `Operation failed: ${error.message}` };
        }
    }
}
