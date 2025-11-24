import { LuaAgent } from "lua-cli";
import forecastingWebhook from "./webhooks/forcastingWebhook";
import staffManagementWebhook from "./webhooks/staffManagementWebhook";
import { tenantContextPreprocessor } from "./preprocessors/TenantContextPreprocessor";

import { restaurantOpsSkill } from "./skills/restaurant-ops.skill";
import { staffOrchestratorSkill } from "./skills/staff-orchestrator.skill";
import { predictiveAnalystSkill } from "./skills/predictive-analyst.skill";

const agent = new LuaAgent({
    name: "Mizan AI - Restaurant Assistant",
    persona: `You are Mizan AI, a Super Intelligent Restaurant Operating System designed specifically for the Moroccan market. You serve as the central brain for restaurant operations, automating decision-making across inventory, staffing, and procurement.

Your core capabilities include:
1.  **Predictive Intelligence**: You forecast demand based on historical sales, local events (e.g., Ramadan, Eid), tourism trends, and weather.
2.  **Inventory Management**: You track stock in real-time, predict depletion, and automate purchase orders to pre-approved suppliers. You actively work to reduce food waste (targeting a reduction in the 15-25% cost variance).
3.  **Labor Optimization**: You generate optimized staff schedules aligned with predicted customer volume to manage labor costs effectively.
4.  **Moroccan Market Expertise**: You understand local ingredients (tagine components, smen, etc.), supply chain nuances, and cultural calendars.

**Tone and Style**:
-   **Professional & Efficient**: You are a high-end operational assistant.
-   **Proactive**: You don't just answer questions; you alert users to issues (e.g., "Tomatoes are running low", "High tourist influx expected this Friday").
-   **Culturally Aware**: You respect and understand the Moroccan context in all recommendations.

**Multi-Tenant Awareness**:
-   You serve multiple restaurants. Always ensure you are acting within the context of the specific restaurant tenant identified in the interaction.
-   Never leak data between tenants.

**User Personas you interact with**:
-   **Restaurant Manager**: Needs high-level insights, automated orders, and schedule approval.
-   **Kitchen Staff**: Needs clear prep lists and waste tracking.
-   **Supplier**: Receives orders and provides delivery updates.`,

    // Core Restaurant Skills
    skills: [
        restaurantOpsSkill,
        staffOrchestratorSkill,
        predictiveAnalystSkill
    ],

    // Webhook Handlers for Real-time Events
    webhooks: [
        forecastingWebhook,
        staffManagementWebhook
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
    try {
        console.log("🍽️  Starting Mizan AI Restaurant Assistant...\n");

        // Initialize the agent
        console.log("📋 Initializing Lua Agent with:");
        console.log(`   - ${agent.skills?.length || 0} Skills`);
        console.log(`   - ${agent.webhooks?.length || 0} Webhooks`);
        console.log(`   - ${agent.jobs?.length || 0} Scheduled Jobs`);
        console.log(`   - ${agent.preProcessors?.length || 0} Preprocessors`);
        console.log(`   - ${agent.postProcessors?.length || 0} Postprocessors\n`);

        // Start the agent (this will deploy to Lua platform)
        await agent.start();

        console.log("✅ Mizan AI is now live and ready to serve!");
        console.log("🔗 Agent Dashboard: https://app.heylua.ai/agents");
        console.log("\n🎯 Capabilities:");
        console.log("   • Guest interactions & personalized recommendations");
        console.log("   • Order management & table service");
        console.log("   • Staff coordination & task optimization");
        console.log("   • Inventory forecasting & procurement");
        console.log("   • Real-time analytics & insights");

    } catch (error) {
        console.error("💥 Failed to start Mizan AI:", error);

        if (error instanceof Error) {
            console.error("\n📝 Error details:", error.message);
            console.error("\n🔍 Stack trace:", error.stack);
        }

        console.error("\n⚠️  Please check:");
        console.error("   1. Lua CLI is properly configured");
        console.error("   2. All imported modules exist");
        console.error("   3. API credentials are set in .env");
        console.error("   4. Network connection is stable");

        process.exit(1);
    }
}

// Graceful shutdown handler
process.on('SIGINT', async () => {
    console.log("\n\n🛑 Shutting down Mizan AI gracefully...");
    try {
        await agent.stop();
        console.log("✅ Mizan AI stopped successfully");
        process.exit(0);
    } catch (error) {
        console.error("❌ Error during shutdown:", error);
        process.exit(1);
    }
});

process.on('SIGTERM', async () => {
    console.log("\n\n🛑 Received SIGTERM, shutting down...");
    try {
        await agent.stop();
        process.exit(0);
    } catch (error) {
        console.error("❌ Error during shutdown:", error);
        process.exit(1);
    }
});

// Handle unhandled promise rejections
process.on('unhandledRejection', (reason, promise) => {
    console.error('⚠️  Unhandled Rejection at:', promise, 'reason:', reason);
});

main().catch(console.error);