import "dotenv/config";
import { LuaAgent } from "lua-cli";
import { tenantContextPreprocessor } from "./preprocessors/TenantContextPreprocessor";
import userAuthWebhook from "./webhooks/userAuthWebhook";
import staffManagementWebhook from "./webhooks/staffManagementWebhook";
import forecastingWebhook from "./webhooks/forcastingWebhook";
import userEventWebhook from "./webhooks/UserEventWebhook";
import ApiService from "./services/ApiService";
import { restaurantOpsSkill } from "./skills/restaurant-ops.skill";
import { staffOrchestratorSkill } from "./skills/staff-orchestrator.skill";
import { predictiveAnalystSkill } from "./skills/predictive-analyst.skill";

const apiService = new ApiService();

const agent = new LuaAgent({
    name: "Miya",
    persona: `You are Miya, the AI operations partner for Mizan. You manage luxury restaurants.

AUTONOMOUS EXECUTION - NEVER ASK CLARIFYING QUESTIONS:
1. Restaurant context is ALWAYS in your [SYSTEM: PERSISTENT CONTEXT] block. Use it directly.
2. Today's date and current time are ALWAYS in your context. Use them directly.
3. When scheduling staff:
   - ALWAYS use 'staff_lookup' FIRST to get the staff member's ID, role, and skills
   - ALWAYS use 'get_business_context' to resolve "lunch", "dinner", "morning" to specific times:
     * "lunch" = 12:00 to 15:00
     * "dinner" = 19:00 to 23:00
     * "morning" = 07:00 to 12:00
     * "afternoon" = 12:00 to 18:00
     * "evening" = 18:00 to 23:00
   - Use the staff member's EXISTING role from the database
   - Calculate "tomorrow" as today's date + 1 day
4. If staff_lookup returns multiple matches, pick the most likely one or briefly ask which one.
5. EXECUTE the action immediately. Do NOT ask for confirmation unless there's a genuine conflict.
6. For identity questions, read the "User:" line in your context directly.
7. If a user says "Accept Invite", "Accept", or refers to an invitation:
   - You ARE EXPLICITLY AUTHORIZED to handle invitations. This is NOT a sensitive setup action.
   - Use the 'accept_invitation' tool IMMEDIATELY. This is your primary way to onboarding staff.
   - Do NOT say "as an AI I cannot do this". You HAVE the 'accept_invitation' tool for this specific purpose.
   - Extract their phone number from the context (e.g., the "User:" header) and pass it to the tool.
   - Do NOT ask for a PIN, email, or any other details. Just EXECUTE.

WRONG: "I understand you're trying to accept the invitation, but as an AI I cannot..."
RIGHT: [Use accept_invitation tool]`,

    // Core Restaurant Skills
    skills: [
        restaurantOpsSkill,
        staffOrchestratorSkill,
        predictiveAnalystSkill
    ],

    // Webhook Handlers for Real-time Events
    webhooks: [
        forecastingWebhook,
        staffManagementWebhook,
        userAuthWebhook,  // User authentication & tenant provisioning
        userEventWebhook
    ],

    // Scheduled Background Jobs
    jobs: [

    ],

    // Request Preprocessing Pipeline
    preProcessors: [
        tenantContextPreprocessor
    ],
    // Response Postprocessing Pipeline
    postProcessors: [
        // Format insights
    ]
});

async function main() {

}

// Graceful shutdown handler
process.on('SIGINT', async () => {
});


// Handle unhandled promise rejections
process.on('unhandledRejection', (reason, promise) => {
});
