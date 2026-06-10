/**
 * ChecklistStarterTool
 * 
 * Initiates the checklist flow for a staff member after clock-in.
 * Fetches assigned checklists for the current shift and creates an execution.
 */

import { LuaTool, env } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { noContextError, upstreamError } from "./_common/errors";

interface ChecklistStep {
    id: string;
    order: number;
    title: string;
    description: string | null;
    step_type: string;
    is_required: boolean;
    requires_photo: boolean;
    requires_note: boolean;
}

interface ChecklistTemplate {
    id: string;
    name: string;
    description: string | null;
    category: string;
    estimated_duration_minutes: number | null;
    total_steps: number;
    steps: ChecklistStep[];
}

interface ShiftChecklistsResponse {
    shift_id: string | null;
    shift_date?: string;
    shift_start?: string;
    checklists: ChecklistTemplate[];
    message: string;
    error?: string;
}

export default class ChecklistStarterTool implements LuaTool {
    name = "checklist_starter";
    description = "Start the checklist flow for a staff member after clock-in. Fetches assigned checklists and initiates the first one.";

    inputSchema = z.object({
        mode: z.enum(["start", "preview"]).describe("'start' to begin the checklist flow, 'preview' to list checklists without starting"),
        trigger: z.enum(["clock_in", "manual"]).optional().default("manual").describe("What triggered the checklist start"),
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
            context?.user?.token ||
            env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');

        if (!token) {
            return noContextError({ hint: "No auth token available for checklist lookup." });
        }

        try {
            const checklistData: ShiftChecklistsResponse = await this.apiService.getShiftChecklists(token);

            if (checklistData.error) {
                return upstreamError(checklistData.error);
            }

            if (!checklistData.checklists || checklistData.checklists.length === 0) {
                return {
                    status: "no_checklists",
                    shift_id: checklistData.shift_id,
                    message: checklistData.message || "No checklists assigned to your shift. You're all set!",
                    next_action: "none"
                };
            }

            const firstChecklist = checklistData.checklists[0];

            if (input.mode === "preview") {
                return {
                    status: "ready",
                    shift_id: checklistData.shift_id,
                    checklists: checklistData.checklists.map(c => ({
                        id: c.id,
                        name: c.name,
                        total_steps: c.total_steps,
                        estimated_duration_minutes: c.estimated_duration_minutes
                    })),
                    message: `You have ${checklistData.checklists.length} checklist(s) to complete.`,
                    next_action: "prompt_user"
                };
            }

            const execution = await this.apiService.createChecklistExecution({
                template_id: firstChecklist.id,
                assigned_shift_id: checklistData.shift_id || undefined
            }, token);

            await this.apiService.startChecklistExecution(execution.id, token);

            const firstStep = firstChecklist.steps[0];

            return {
                status: "started",
                execution_id: execution.id,
                checklist: {
                    id: firstChecklist.id,
                    name: firstChecklist.name,
                    total_steps: firstChecklist.total_steps,
                    estimated_duration_minutes: firstChecklist.estimated_duration_minutes
                },
                current_step: {
                    index: 1,
                    total: firstChecklist.total_steps,
                    id: firstStep.id,
                    title: firstStep.title,
                    description: firstStep.description,
                    requires_photo: firstStep.requires_photo
                },
                remaining_checklists: checklistData.checklists.length - 1,
                message: `Starting ${firstChecklist.name}. ${firstChecklist.total_steps} items to complete.`,
                next_action: "present_step"
            };

        } catch (error: any) {
            console.error("[ChecklistStarterTool] Error:", error.message);
            return upstreamError(error.message);
        }
    }
}
