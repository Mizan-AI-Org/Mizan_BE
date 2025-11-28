import ScheduleOptimizerTool from "./src/skills/tools/ScheduleOptimizerTool";
import ApiService from "./src/services/ApiService";
// Mock ApiService
class MockApiService extends ApiService {
    async optimizeSchedule(data, token) {
        return {
            status: 'success',
            message: `Optimized for ${data.week_start}`,
            shifts: [],
            optimization_metrics: {}
        };
    }
}
async function runVerification() {
    console.log("🚀 Starting Context Fallback Verification");
    const mockApi = new MockApiService();
    const tool = new ScheduleOptimizerTool(mockApi);
    // Test 1: Context with 'get' method (Standard)
    console.log("\n--- Test 1: Standard Context (get method) ---");
    const context1 = {
        get: (key) => key === "restaurantId" ? "rest-standard" : null,
        metadata: { token: "mock-token" }
    };
    const result1 = await tool.execute({ week_start: "2025-12-01" }, context1);
    console.log("Result 1:", result1.status);
    // Test 2: Context with metadata.restaurantId (Fallback)
    console.log("\n--- Test 2: Metadata Context (Fallback) ---");
    const context2 = {
        metadata: {
            token: "mock-token",
            restaurantId: "rest-metadata"
        }
    };
    const result2 = await tool.execute({ week_start: "2025-12-01" }, context2);
    console.log("Result 2:", result2.status);
    // Test 3: Missing Context
    console.log("\n--- Test 3: Missing Context ---");
    const context3 = { metadata: { token: "mock-token" } };
    const result3 = await tool.execute({ week_start: "2025-12-01" }, context3);
    console.log("Result 3:", result3.status);
}
runVerification().catch(console.error);
