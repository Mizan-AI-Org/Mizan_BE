import { LuaTool, User, env } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

export default class StaffSchedulerTool implements LuaTool {
    name = "staff_scheduler";
    description = "Schedule staff shifts. Use 'my_shifts' action when the user asks about THEIR OWN shifts. ALWAYS extract the restaurant ID from your [SYSTEM: PERSISTENT CONTEXT] block and pass it as restaurantId. For time periods like 'lunch', use 12:00-15:00; 'dinner' use 19:00-23:00.";

    inputSchema = z.object({
        action: z.enum(["list_shifts", "create_shift", "update_shift", "check_availability", "get_staff", "my_shifts"]).describe("The action to perform. Use 'my_shifts' for the user's own shifts."),
        staff_names: z.array(z.string()).optional().describe("Names of the staff members (fuzzy match). Support multiple names to avoid 'single staff' errors."),
        staff_name: z.string().optional().describe("DEPRECATED: Use staff_names instead. Name of a single staff member."),
        date: z.string().optional().describe("Date of the shift (YYYY-MM-DD)."),
        start_time: z.string().optional().describe("Start time (HH:MM)."),
        end_time: z.string().optional().describe("End time (HH:MM)."),
        role: z.string().optional().describe("Role for the shift"),
        shift_id: z.string().optional().describe("ID of the shift to update"),
        notes: z.string().optional().describe("Notes for the shift"),
        workspace_location: z.string().optional().describe("Specific workspace/station assignment (e.g., 'Kitchen', 'Bar', 'Terrace')"),
        restaurantId: z.string().describe("The restaurant ID from your context"),
        is_recurring: z.boolean().optional().describe("Whether this shift should be recurring"),
        frequency: z.enum(["DAILY", "WEEKLY"]).optional().describe("Recurrence frequency"),
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    private normalizeName(name: string): string {
        if (!name) return "";
        const titleRegex = /^(?:mr\.?|mrs\.?|ms\.?|miss\.?|dr\.?|prof\.?|sir|madam|mx\.?)\s+/i;
        return name.trim().replace(titleRegex, "").toLowerCase();
    }

    private findStaffMember(staffList: any[], name: string): any[] {
        const searchName = this.normalizeName(name);
        if (!searchName) return [];

        return staffList.filter((s: any) => {
            const fullName = `${s.first_name} ${s.last_name}`.toLowerCase();
            const firstName = (s.first_name || "").toLowerCase();
            const lastName = (s.last_name || "").toLowerCase();

            return fullName.includes(searchName) ||
                firstName.includes(searchName) ||
                lastName.includes(searchName);
        });
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
        const metadata = profile.metadata && typeof profile.metadata === 'object' ? profile.metadata : {};

        // Check multiple sources for restaurantId (widget sends metadata.restaurantId via LuaPop.init)
        const restaurantId =
            input.restaurantId ||
            (user as any).restaurantId ||
            userData.restaurantId ||
            profile.restaurantId ||
            profile.restaurant_id ||
            (metadata as any).restaurantId ||
            (metadata as any).restaurant_id;

        // Token retrieval with service account fallback
        const token =
            (user as any).token ||
            userData.token ||
            profile.token ||
            profile.accessToken ||
            profile.credentials?.accessToken ||
            (metadata as any).token ||
            (metadata as any).accessToken ||
            env('MIZAN_SERVICE_TOKEN'); // Service account fallback

        // Agent key for agent-authenticated endpoints
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');

        if (!restaurantId) {
            console.error('[StaffSchedulerTool] Missing restaurant context.');
            return {
                status: "error",
                message: "I don't have your restaurant context. Please make sure you're logged in through the Mizan app."
            };
        }

        try {
            switch (input.action) {
                case "my_shifts": {
                    // Self-lookup: use the user's staffId from context
                    const myStaffId =
                        userData.staffId ||
                        profile.staffId ||
                        user.staffId ||
                        userData.user_id ||
                        profile.user_id;

                    if (!myStaffId) {
                        console.error("[StaffSchedulerTool] my_shifts: No staffId found in user context.");
                        return {
                            status: "error",
                            message: "I can't seem to find your staff profile to retrieve your shifts. There might be a slight delay in the system updating after you accepted the invitation, or there could be an issue with how your name is registered. Please try again in a little while, or contact your manager if the issue persists."
                        };
                    }

                    const params: any = { staff_id: myStaffId };

                    // Default: current week + next week
                    const today = new Date();
                    params.date_from = today.toISOString().split('T')[0];
                    const twoWeeksLater = new Date(today.getTime() + 14 * 24 * 60 * 60 * 1000);
                    params.date_to = twoWeeksLater.toISOString().split('T')[0];

                    if (input.date) {
                        params.date_from = input.date;
                        params.date_to = input.date;
                    }

                    console.log(`[StaffSchedulerTool] my_shifts: Fetching shifts for staffId=${myStaffId}`);
                    const shifts = await apiService.getAssignedShifts(params, token);
                    const results = shifts.results || shifts;

                    if (!results || results.length === 0) {
                        return {
                            status: "success",
                            message: "You have no shifts scheduled for the requested period.",
                            shifts: []
                        };
                    }

                    return {
                        status: "success",
                        message: `Found ${results.length} shift(s) for you.`,
                        shifts: results
                    };
                }

                case "get_staff": {
                    const staff = await apiService.getStaffListForAgent(restaurantId, undefined, token);
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

                    const namesToSearch = input.staff_names || (input.staff_name ? [input.staff_name] : []);
                    if (namesToSearch.length > 0) {
                        const allMatches: any[] = [];
                        for (const name of namesToSearch) {
                            const backendMatches = await apiService.getStaffListForAgent(restaurantId, name, token);
                            allMatches.push(...backendMatches);
                        }

                        if (allMatches.length === 1) {
                            params.staff_id = allMatches[0].id;
                        } else if (allMatches.length > 1) {
                            // Deduplicate matches by ID
                            const uniqueMatches = Array.from(new Map(allMatches.map(m => [m.id, m])).values());
                            if (uniqueMatches.length === 1) {
                                params.staff_id = uniqueMatches[0].id;
                            } else {
                                return {
                                    status: "multiple_results",
                                    message: `I found ${uniqueMatches.length} staff members matching your request. Which one did you mean?`,
                                    matches: uniqueMatches.map((s: any) => ({ id: s.id, name: `${s.first_name} ${s.last_name}`, role: s.role }))
                                };
                            }
                        } else {
                            return { status: "error", message: `Staff member(s) '${namesToSearch.join(", ")}' not found.` };
                        }
                    }

                    params.restaurant_id = restaurantId;
                    const shifts = await apiService.getAssignedShiftsForAgent(params, token);
                    return {
                        status: "success",
                        shifts: shifts.results || shifts
                    };
                }

                case "create_shift": {
                    const namesToSearch = input.staff_names || (input.staff_name ? [input.staff_name] : []);
                    if (namesToSearch.length === 0 || !input.date || !input.start_time || !input.end_time) {
                        return { status: "error", message: "Missing required fields: staff names, date, start_time, end_time" };
                    }

                    const staffToSchedule = [];
                    const missingNames = [];

                    for (const name of namesToSearch) {
                        const matches = await apiService.getStaffListForAgent(restaurantId, name, token);
                        if (matches.length === 1) {
                            staffToSchedule.push(matches[0]);
                        } else if (matches.length > 1) {
                            return {
                                status: "multiple_results",
                                message: `I found multiple matches for '${name}'. Please be more specific.`,
                                matches: matches.map((s: any) => ({ id: s.id, name: `${s.first_name} ${s.last_name}`, role: s.role }))
                            };
                        } else {
                            missingNames.push(name);
                        }
                    }

                    if (missingNames.length > 0) {
                        // Get a small sample of staff to help the user
                        const staffList = await apiService.getStaffListForAgent(restaurantId, undefined, token);
                        const allAvailable = staffList.slice(0, 5).map((s: any) => `${s.first_name} ${s.last_name}`).join(", ");
                        return {
                            status: "error",
                            message: `I couldn't find: ${missingNames.join(", ")}. I found these staff members instead: ${allAvailable}${staffList.length > 5 ? "..." : ""}.`
                        };
                    }

                    const datesToSchedule = [input.date];
                    if (input.is_recurring && input.frequency) {
                        const baseDate = new Date(input.date);
                        for (let i = 1; i <= 3; i++) {
                            const nextDate = new Date(baseDate);
                            if (input.frequency === "DAILY") nextDate.setDate(baseDate.getDate() + i);
                            else if (input.frequency === "WEEKLY") nextDate.setDate(baseDate.getDate() + (i * 7));
                            datesToSchedule.push(nextDate.toISOString().split('T')[0]);
                        }
                    }

                    const results = [];
                    for (const staffMember of staffToSchedule) {
                        const finalRole = input.role || staffMember.role || "SERVER";
                        for (const shiftDate of datesToSchedule) {
                            try {
                                const result = await apiService.createShiftForAgent({
                                    restaurant_id: restaurantId,
                                    staff_id: staffMember.id,
                                    shift_date: shiftDate,
                                    start_time: input.start_time,
                                    end_time: input.end_time,
                                    role: finalRole,
                                    notes: input.notes,
                                    workspace_location: input.workspace_location
                                }, token);

                                if (result.success) {
                                    results.push({ name: staffMember.first_name, date: shiftDate, status: "success" });
                                    if (staffMember.phone) {
                                        await apiService.sendShiftNotification({ shift_id: result.shift.id, staff_id: staffMember.id }).catch(() => { });
                                    }
                                } else {
                                    results.push({ name: staffMember.first_name, date: shiftDate, status: "error", message: result.error });
                                }
                            } catch (err: any) {
                                results.push({ name: staffMember.first_name, date: shiftDate, status: "error", message: err.message });
                            }
                        }
                    }

                    const successCount = results.filter(r => r.status === "success").length;
                    const errors = results.filter(r => r.status === "error");

                    if (successCount === 0) {
                        const firstError = errors[0];
                        return { status: "error", message: `Failed: ${firstError.name} on ${firstError.date}: ${firstError.message}` };
                    }

                    return {
                        status: "success",
                        message: `Successfully scheduled ${successCount} shift(s) for ${staffToSchedule.map((s: any) => s.first_name).join(", ")}.`,
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
                    const staffList = await apiService.getStaffListForAgent(restaurantId, undefined, token);
                    const matches = this.findStaffMember(staffList, input.staff_name);

                    if (matches.length === 0) return { status: "error", message: `Staff member '${input.staff_name}' not found.` };
                    if (matches.length > 1) return { status: "multiple_results", matches: matches.map(m => m.first_name) };

                    const staffMember = matches[0];
                    const conflicts = await apiService.detectConflicts({
                        staff_id: staffMember.id,
                        shift_date: input.date,
                        start_time: input.start_time,
                        end_time: input.end_time,
                        workspace_location: input.workspace_location
                    });

                    return { status: "success", available: !conflicts.has_conflicts, conflicts: conflicts.conflicts };
                }

                default:
                    return { status: "error", message: "Invalid action" };
            }
        } catch (error: any) {
            const msg = error.message || "";
            if (/restaurant context|resolve restaurant|Unable to resolve/i.test(msg)) {
                return {
                    status: "error",
                    message: "I couldn't access your restaurant settings for scheduling right now. Please try again in a moment, or make sure you're logged in through the Mizan dashboard."
                };
            }
            return { status: "error", message: `The scheduling system encountered a technical error: ${msg}. Please report this if it persists.` };
        }
    }
}
