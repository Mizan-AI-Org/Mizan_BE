import { LuaTool } from "lua-cli";
import { z } from "zod";

export default class InventoryTool implements LuaTool {
    name = "inventory_manager";
    description = "Manage restaurant inventory, check stock levels, and track waste.";

    inputSchema = z.object({
        action: z.enum(["check_stock", "log_waste", "get_alerts"]),
        item: z.string().optional(),
        quantity: z.number().optional(),
        unit: z.string().optional(),
        restaurantId: z.string().optional().describe("The ID of the restaurant (from context)"),
        restaurantName: z.string().optional().describe("The name of the restaurant (from context)")
    });

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        const restaurantId = input.restaurantId || (context?.get ? context.get("restaurantId") : undefined);
        const restaurantName = input.restaurantName || (context?.get ? context.get("restaurantName") : "Unknown Restaurant");

        if (!restaurantId) {
            return { status: "error", message: "No restaurant context found. Please ensure you are logged in." };
        }

        console.log(`[InventoryTool] Executing for ${restaurantName} (${restaurantId})`);

        // Simulated logic for demonstration
        if (input.action === "check_stock") {
            return {
                status: "success",
                restaurant: restaurantName,
                item: input.item,
                current_stock: "15kg",
                status_level: "OK",
                message: `Stock for ${input.item} is sufficient at ${restaurantName}.`
            };
        }

        if (input.action === "log_waste") {
            return {
                status: "success",
                restaurant: restaurantName,
                message: `Logged ${input.quantity}${input.unit} of ${input.item} as waste for ${restaurantName}.`,
                recommendation: "Consider reducing prep for this item by 10% next week."
            };
        }

        if (input.action === "get_alerts") {
            return {
                restaurant: restaurantName,
                alerts: [
                    { item: "Tomatoes", level: "CRITICAL", message: "Stock below 2kg. Reorder immediately." },
                    { item: "Lamb", level: "LOW", message: "Stock below 5kg. Reorder suggested." }
                ]
            };
        }

        return { status: "error", message: "Invalid action" };
    }
}
