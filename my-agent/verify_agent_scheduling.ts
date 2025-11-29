import StaffSchedulerTool from "./src/skills/tools/StaffSchedulerTool";
import ApiService from "./src/services/ApiService";

// Mock ApiService
class MockApiService extends ApiService {
    async getStaffList(restaurantId: string, token: string) {
        console.log(`[Mock] getStaffList called for ${restaurantId}`);
        return [
            { id: "staff-1", first_name: "John", last_name: "Doe", role: "server" },
            { id: "staff-2", first_name: "Jane", last_name: "Smith", role: "chef" }
        ];
    }

    async getAssignedShifts(params: any, token: string) {
        console.log(`[Mock] getAssignedShifts called with params:`, params);
        return {
            results: [
                { id: "shift-1", staff: { id: "staff-1", first_name: "John" }, shift_date: "2023-10-27", start_time: "10:00", end_time: "18:00" }
            ]
        };
    }

    async createAssignedShift(data: any, token: string) {
        console.log(`[Mock] createAssignedShift called with data:`, data);
        return {
            id: "new-shift-1",
            ...data,
            status: "SCHEDULED"
        };
    }

    async detectConflicts(params: any, token: string) {
        console.log(`[Mock] detectConflicts called with params:`, params);
        // Simulate conflict for Jane on a specific date
        if (params.staff_id === "staff-2" && params.shift_date === "2023-10-28") {
            return { has_conflicts: true, conflicts: ["Overlap with existing shift"] };
        }
        return { has_conflicts: false, conflicts: [] };
    }
}

async function runVerification() {
    console.log("🚀 Starting StaffSchedulerTool Verification");

    const mockApi = new MockApiService();
    const tool = new StaffSchedulerTool(mockApi);

    const context = {
        get: (key: string) => {
            if (key === "restaurantId") return "rest-123";
            return null;
        },
        metadata: { token: "mock-token" }
    };

    // Test 1: List Shifts
    console.log("\n--- Test 1: List Shifts ---");
    const listResult = await tool.execute({
        action: "list_shifts",
        date: "2023-10-27"
    }, context);
    console.log("Result:", JSON.stringify(listResult, null, 2));

    // Test 2: Create Shift (Success)
    console.log("\n--- Test 2: Create Shift (Success) ---");
    const createResult = await tool.execute({
        action: "create_shift",
        staff_name: "John",
        date: "2023-10-29",
        start_time: "09:00",
        end_time: "17:00"
    }, context);
    console.log("Result:", JSON.stringify(createResult, null, 2));

    // Test 3: Create Shift (Conflict)
    console.log("\n--- Test 3: Create Shift (Conflict) ---");
    const conflictResult = await tool.execute({
        action: "create_shift",
        staff_name: "Jane",
        date: "2023-10-28", // Configured to conflict
        start_time: "09:00",
        end_time: "17:00"
    }, context);
    console.log("Result:", JSON.stringify(conflictResult, null, 2));

    // Test 4: Check Availability
    console.log("\n--- Test 4: Check Availability ---");
    const availResult = await tool.execute({
        action: "check_availability",
        staff_name: "John",
        date: "2023-10-30",
        start_time: "10:00",
        end_time: "14:00"
    }, context);
    console.log("Result:", JSON.stringify(availResult, null, 2));
}

runVerification().catch(console.error);
