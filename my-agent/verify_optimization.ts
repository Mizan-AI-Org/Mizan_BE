import ScheduleOptimizerTool from "./src/skills/tools/ScheduleOptimizerTool";
import ApiService from "./src/services/ApiService";

// Mock ApiService
class MockApiService extends ApiService {
    async optimizeSchedule(data: any, token: string) {
        console.log(`[Mock] optimizeSchedule called with data:`, data);

        if (data.department === 'kitchen') {
            return {
                status: 'success',
                message: `Generated 14 shifts for week of ${data.week_start}`,
                shifts: [
                    { date: '2025-12-01', time: '11:00-15:00', staff: 'Chef Ramsey', role: 'CHEF' },
                    { date: '2025-12-01', time: '17:00-22:00', staff: 'Sous Chef', role: 'KITCHEN_STAFF' }
                ],
                optimization_metrics: {
                    staff_utilization: '90%',
                    coverage: '100%',
                    overtime_hours: 0
                }
            };
        }

        return {
            status: 'success',
            message: `Generated 0 shifts`,
            shifts: [],
            optimization_metrics: {}
        };
    }
}

async function runVerification() {
    console.log("🚀 Starting ScheduleOptimizerTool Verification");

    const mockApi = new MockApiService();
    const tool = new ScheduleOptimizerTool(mockApi);

    const context = {
        get: (key: string) => {
            if (key === "restaurantId") return "rest-123";
            return null;
        },
        metadata: { token: "mock-token" }
    };

    // Test 1: Optimize Kitchen Schedule
    console.log("\n--- Test 1: Optimize Kitchen Schedule ---");
    const result = await tool.execute({
        week_start: "2025-12-01",
        department: "kitchen"
    }, context);
    console.log("Result:", JSON.stringify(result, null, 2));

    // Test 2: Missing Context
    console.log("\n--- Test 2: Missing Context ---");
    const errorResult = await tool.execute({
        week_start: "2025-12-01"
    }, {}); // Empty context
    console.log("Result:", JSON.stringify(errorResult, null, 2));
}

runVerification().catch(console.error);
