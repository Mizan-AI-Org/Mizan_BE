import { LuaTool, User, Lua, env } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

export default class ScheduleOptimizerTool implements LuaTool {
    name = "schedule_optimizer";
    description = "Optimize staff schedules based on predicted demand and staff availability.";

    inputSchema = z.object({
        week_start: z.string().describe("Start date of the week (YYYY-MM-DD)"),
        department: z.enum(["kitchen", "service", "all"]).optional().describe("Department to optimize"),
        restaurantId: z.string().optional().describe("Restaurant ID (will use context if not provided)")
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    async execute(input: z.infer<typeof this.inputSchema>) {
        // DEBUG: Intensive log for auth failure investigation
        console.log('[ScheduleOptimizerTool] V7 DEBUG START');
        console.log('[ScheduleOptimizerTool] Channel:', Lua.request.channel);

        // Use User.get() as discovered in the library source
        const user = await User.get();
        if (!user) {
            return {
                status: "error",
                message: `[V8 DIAGNOSTIC] No user context found. (channel=${Lua.request.channel})`
            };
        }
        const userData = (user as any).data || {};
        const profile = (user as any)._luaProfile || {};

        console.log('[ScheduleOptimizerTool] User Keys:', Object.keys(user).join(', '));
        console.log('[ScheduleOptimizerTool] User Data Keys:', Object.keys(userData).join(', '));
        console.log('[ScheduleOptimizerTool] Profile Keys:', Object.keys(profile).join(', '));

        // Priority: input parameter > user data > profile
        const restaurantId =
            input.restaurantId ||
            user.restaurantId ||
            userData.restaurantId ||
            profile.restaurantId;

        // Try to get token from multiple sources, including re-extracting from user.data if needed
        let token =
            user.token ||
            userData.token ||
            profile.token ||
            profile.accessToken ||
            profile.credentials?.accessToken ||
            (user as any).accessToken ||
            (profile as any).sessionToken;

        // If still no token, try to extract from user.data string representation (fallback)
        if (!token && userData && typeof userData === 'object') {
            const dataStr = JSON.stringify(userData);
            const tokenMatch = dataStr.match(/"token"\s*:\s*"([^"]+)"/);
            if (tokenMatch && tokenMatch[1]) {
                token = tokenMatch[1];
                console.log('[ScheduleOptimizerTool] 🔑 Extracted token from userData JSON');
            }
        }

        // Service account fallback (primarily for API/dev contexts)
        if (!token) {
            token = env('MIZAN_SERVICE_TOKEN') || process.env.MIZAN_SERVICE_TOKEN;
        }

        console.log('[ScheduleOptimizerTool] Identified restaurantId:', restaurantId);
        console.log('[ScheduleOptimizerTool] Identified token:', token ? 'FOUND' : 'NOT FOUND');

        if (!restaurantId) {
            return {
                status: "error",
                message: `[V8 DIAGNOSTIC] Restaurant context missing. (UserKeys: ${Object.keys(user).join(',')}, DataKeys: ${Object.keys(userData).join(',')})`
            };
        }

        if (!token) {
            const userKeys = Object.keys(user).join(',');
            const dataKeys = Object.keys(userData).join(',');
            const profKeys = Object.keys(profile).join(',');

            return {
                status: "error",
                message: `[V8 DIAGNOSTIC] Authentication token missing. On the web widget, Miya should receive your Mizan access token via runtimeContext/user profile; on API/dev, configure MIZAN_SERVICE_TOKEN. (channel=${Lua.request.channel}) (UserKeys: ${userKeys}, DataKeys: ${dataKeys}, ProfKeys: ${profKeys})`
            };
        }

        const userToken = token;

        try {
            console.log(`[ScheduleOptimizerTool] Optimizing for ${restaurantId}, week: ${input.week_start}`);

            const result = await this.apiService.optimizeSchedule({
                week_start: input.week_start,
                department: input.department
            }, userToken);

            return {
                status: "success",
                message: result.message,
                shifts_generated: result.shifts.length,
                metrics: result.optimization_metrics,
                schedule_summary: result.shifts.map((s: any) => `${s.date} ${s.time}: ${s.staff} (${s.role || 'Staff'})`).slice(0, 10) // Limit summary
            };
        } catch (error: any) {
            console.error("[ScheduleOptimizerTool] Optimization failed:", error.message);
            return {
                status: "error",
                message: `Optimization failed: ${error.message}`
            };
        }
    }
}
