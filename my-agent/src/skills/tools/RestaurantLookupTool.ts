import { LuaTool } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

export default class RestaurantLookupTool implements LuaTool {
    name = "get_restaurant_context";
    description = "Look up restaurant ID by name. Use this when you know the restaurant name but need the ID.";

    inputSchema = z.object({
        restaurant_name: z.string().describe("Name of the restaurant (e.g., 'Barometre')")
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        const token = context?.metadata?.token || (context?.get ? context.get("token") : undefined);

        if (!token) {
            return {
                status: "error",
                message: "Authentication required to look up restaurant information."
            };
        }

        try {
            console.log(`[RestaurantLookupTool] Looking up restaurant: ${input.restaurant_name}`);

            // Query the API to find restaurant by name
            const response = await this.apiService.axiosInstance.get('/api/restaurants/', {
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                },
                params: {
                    search: input.restaurant_name
                }
            });

            const restaurants = response.data.results || response.data;

            if (!restaurants || restaurants.length === 0) {
                return {
                    status: "error",
                    message: `No restaurant found with name '${input.restaurant_name}'`
                };
            }

            // Find exact match or closest match
            const match = restaurants.find((r: any) =>
                r.name.toLowerCase() === input.restaurant_name.toLowerCase()
            ) || restaurants[0];

            console.log(`[RestaurantLookupTool] Found restaurant: ${match.name} (${match.id})`);

            return {
                status: "success",
                restaurant: {
                    id: match.id,
                    name: match.name,
                    address: match.address
                },
                message: `Found restaurant: ${match.name}`
            };
        } catch (error: any) {
            console.error("[RestaurantLookupTool] Lookup failed:", error.message);
            return {
                status: "error",
                message: `Failed to look up restaurant: ${error.message}`
            };
        }
    }
}
