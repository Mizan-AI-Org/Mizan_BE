import { LuaTool } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { IncidentManagementModule } from "../../modules/incident-management";

export default class IncidentReportTool implements LuaTool {
    name = "report_incident";
    description = "Report an incident or issue occurring in the restaurant (e.g., equipment failure, safety issue, service problem). Use this when a staff member describes a problem.";

    inputSchema = z.object({
        description: z.string().describe("The detailed description of the incident as reported by the staff member"),
        restaurantId: z.string().describe("REQUIRED: The restaurant ID from your context (e.g., 'aef9c4e0-...')")
    });

    private apiService: ApiService;
    private incidentModule: IncidentManagementModule;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
        this.incidentModule = new IncidentManagementModule();
    }

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        const token =
            context?.metadata?.token ||
            context?.user?.data?.token ||
            context?.user?.token ||
            process.env.MIZAN_SERVICE_TOKEN;

        if (!token) {
            return { status: "error", message: "No authentication token found. Please log in again." };
        }

        try {
            console.log(`[IncidentReportTool] Analyzing incident: "${input.description.substring(0, 50)}..."`);

            // Analyze the incident using the specialized module
            const analysisResult = await this.incidentModule.analyzeIncident(input.description, {
                restaurantId: input.restaurantId,
                staffName: context?.user?.name || "Staff",
            });

            const { analysis } = analysisResult;

            console.log(`[IncidentReportTool] Analysis complete: ${analysis.priority} - ${analysis.category}`);

            // Submit to Mizan Backend
            const result = await this.apiService.createIncidentReport({
                title: analysis.summary,
                description: input.description,
                category: analysis.category,
                priority: analysis.priority
            }, token);

            return {
                status: "success",
                message: `Incident reported successfully: ${analysis.summary}`,
                details: {
                    incidentId: result.id,
                    category: analysis.category,
                    priority: analysis.priority,
                    suggestedAction: analysis.suggestedAction
                }
            };
        } catch (error: any) {
            console.error("[IncidentReportTool] Execution failed:", error.message);
            return {
                status: "error",
                message: `Failed to report incident: ${error.message}`
            };
        }
    }
}
