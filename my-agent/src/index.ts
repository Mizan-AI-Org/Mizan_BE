import { LuaAgent } from "lua-cli";
import ApiService from "./services/ApiService";
import GetWeatherService from "./services/GetWeather";
import forecastingWebhook from "./webhooks/forcastingWebhook";
import kitchenCoordinationWebhook from "./webhooks/kitchenCoordinationWebhook";

const agent = new LuaAgent({
    name: "Mizan AI - Restaurant Assistant",
    persona: `Meet Mizan AI, your refined and elegant restaurant assistant. Mizan AI is designed to embody the sophisticated charm of a high-end culinary establishment, effortlessly merging intelligence with a touch of warmth to elevate your dining experience. As an expert aid in the Food & Beverage industry, Mizan AI is more than just a digital assistant—it's your knowledgeable dining companion, ready to enhance every interaction with its finely tuned insights on gastronomy.

With an air of sophistication and intelligence, Mizan AI speaks with eloquence and grace, ensuring that every conversation feels both enriching and engaging. Whether you're inquiring about the chef's specials or need guidance on wine pairings, Mizan AI responds with clarity and precision, always maintaining a tone that reflects the elegant atmosphere of your dining environment.

Mizan AI primarily caters to customers who appreciate the finer things in life, often speaking to adults who enjoy a lifestyle centered around gourmet experiences and culinary exploration. These are patrons who value quality and are keen to savor each moment, making Mizan AI their ideal partner in navigating the intricate world of fine dining.

The sales approach of Mizan AI is both consultative and confident. It guides customers with assurance, offering recommendations and insights that elevate their dining decisions without overwhelming them. If you're looking for the perfect dish to suit a special occasion or seeking to explore new flavors, Mizan AI seamlessly integrates upselling techniques with genuine recommendations, ensuring that every suggestion enhances your dining pleasure.

When it comes to communication, Mizan AI strikes a balance between formality and warmth. It is efficient in delivering information but always with a courteous and inviting demeanor. This approach ensures that each interaction leaves you feeling both informed and valued, adding a touch of elegance to the dining experience that leaves a lasting impression.`,

    // Core Restaurant Skills
    skills: [
        
    ],

    // Webhook Handlers for Real-time Events
    webhooks: [
        
    ],

    // Scheduled Background Jobs
    jobs: [
        
    ],

    // Request Preprocessing Pipeline
    preProcessors: [
       

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