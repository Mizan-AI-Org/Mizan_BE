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
        restaurantId: z.string().describe("The ID of the restaurant tenant"),
    });

    async execute(input: z.infer<typeof this.inputSchema>) {
        // Simulated logic for demonstration
        if (input.action === "check_stock") {
            return {
                status: "success",
                item: input.item,
                current_stock: "15kg",
                status_level: "OK",
                message: `Stock for ${input.item} is sufficient.`
            };
        }

        if (input.action === "log_waste") {
            return {
                status: "success",
                message: `Logged ${input.quantity}${input.unit} of ${input.item} as waste.`,
                recommendation: "Consider reducing prep for this item by 10% next week."
            };
        }

        if (input.action === "get_alerts") {
            return {
                alerts: [
                    { item: "Tomatoes", level: "CRITICAL", message: "Stock below 2kg. Reorder immediately." },
                    { item: "Lamb", level: "LOW", message: "Stock below 5kg. Reorder suggested." }
                ]
            };
        }

        return { status: "error", message: "Invalid action" };
    }
}
