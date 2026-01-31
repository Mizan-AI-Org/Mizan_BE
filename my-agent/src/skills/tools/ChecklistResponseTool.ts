/**
 * ChecklistResponseTool
 * 
 * Processes staff responses (YES/NO/NA) to checklist items.
 * Handles photo evidence when required and advances to the next step.
 */

import { LuaTool } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

export default class ChecklistResponseTool implements LuaTool {
    name = "process_checklist_response";
    description = "Process a staff member's response to a checklist item (YES, NO, or NA). Handles photo evidence and advances to next step.";

    inputSchema = z.object({
        execution_id: z.string().describe("The checklist execution ID"),
        step_id: z.string().describe("The current step ID being responded to"),
        response: z.enum(["YES", "NO", "NA"]).describe("Staff response to the checklist item"),
        photo_url: z.string().optional().describe("URL of photo evidence if provided"),
        notes: z.string().optional().describe("Additional notes from staff"),
        all_steps: z.array(z.object({
            id: z.string(),
            order: z.number(),
            title: z.string(),
            requires_photo: z.boolean()
        })).describe("All steps in the checklist for navigation"),
        current_index: z.number().describe("Current step index (1-based)")
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        const token =
            context?.metadata?.token ||
            (context?.get ? context.get("token") : undefined) ||
            context?.user?.data?.token ||
            context?.user?.token;

        if (!token) {
            console.log("[ChecklistResponseTool] No user token found, proceeding with agent authentication");
        }

        try {
            // Determine if photo is required for this step
            const currentStep = input.all_steps.find(s => s.id === input.step_id);
            const requiresPhoto = currentStep?.requires_photo || false;

            // If photo is required but not provided, request it
            if (requiresPhoto && !input.photo_url) {
                return {
                    status: "awaiting_photo",
                    execution_id: input.execution_id,
                    step_id: input.step_id,
                    pending_response: input.response,
                    message: "📷 Please send a photo as evidence for this item.",
                    next_action: "request_photo"
                };
            }

            // Build sync data for the step response
            const syncData = {
                step_responses: [{
                    step_id: input.step_id,
                    response: input.response,
                    status: "COMPLETED",
                    is_completed: true,
                    notes: input.notes || null,
                    responded_at: new Date().toISOString()
                }],
                evidence: input.photo_url ? [{
                    step_response_id: input.step_id,
                    evidence_type: "PHOTO",
                    file_path: input.photo_url,
                    filename: `evidence_${input.step_id}.jpg`,
                    file_size: 0,
                    mime_type: "image/jpeg",
                    metadata: { source: "whatsapp" }
                }] : []
            };

            // Submit the response via sync endpoint
            if (token) {
                await this.apiService.syncChecklistResponse(input.execution_id, syncData, token);
            } else {
                await this.apiService.syncChecklistResponseForAgent(input.execution_id, syncData);
            }

            // Calculate next step
            const totalSteps = input.all_steps.length;
            const nextIndex = input.current_index + 1;
            const isComplete = nextIndex > totalSteps;

            if (isComplete) {
                // Complete the checklist
                try {
                    if (token) {
                        await this.apiService.completeChecklistExecution(
                            input.execution_id,
                            "Completed via WhatsApp",
                            token
                        );
                    } else {
                        await this.apiService.completeChecklistExecutionForAgent(
                            input.execution_id,
                            "Completed via WhatsApp (Agent)"
                        );
                    }
                } catch (e) {
                    // Might fail if validation issues, but continue
                    console.warn("Could not auto-complete execution:", e);
                }

                return {
                    status: "checklist_complete",
                    execution_id: input.execution_id,
                    total_steps: totalSteps,
                    message: `✅ Checklist Complete! All ${totalSteps} items completed.`,
                    summary: {
                        total_items: totalSteps,
                        last_response: input.response,
                        has_photo: !!input.photo_url
                    },
                    next_action: "show_summary"
                };
            }

            // Get next step
            const nextStep = input.all_steps.find(s => s.order === nextIndex);

            return {
                status: "step_saved",
                execution_id: input.execution_id,
                completed_step: {
                    id: input.step_id,
                    response: input.response,
                    had_photo: !!input.photo_url
                },
                next_step: nextStep ? {
                    index: nextIndex,
                    total: totalSteps,
                    id: nextStep.id,
                    title: nextStep.title,
                    requires_photo: nextStep.requires_photo
                } : null,
                progress: `${input.current_index}/${totalSteps}`,
                message: "✓ Recorded.",
                next_action: "present_next_step"
            };

        } catch (error: any) {
            console.error("[ChecklistResponseTool] Error:", error.message);
            return {
                status: "error",
                message: `Failed to save response: ${error.message}`,
                retry: true
            };
        }
    }
}
