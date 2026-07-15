/**
 * ChecklistRespondTool
 *
 * Records Yes/No/N/A for the current shift checklist task and returns the next
 * prompt (or a photo-proof request). Miya owns WhatsApp delivery for text;
 * inbound photos are handled by the Django webhook after awaiting_photo.
 */

import { LuaTool, User, env } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import {
    formatChecklistComplete,
    formatChecklistTaskPrompt,
    formatPhotoAwaitPrompt,
} from "../../utils/checklistMessages";
import { resolveStaffPhoneForByPhoneTools } from "../../utils/resolveStaffPhoneFromLuaUser";

export default class ChecklistRespondTool implements LuaTool {
    name = "checklist_respond";
    description =
        "Record a staff member's response (Yes, No, or N/A) to their current checklist task " +
        "and get the next step. After Yes, some tasks return status=awaiting_photo — SEND that " +
        "photo request and wait; do NOT invent the next task until they send the photo. " +
        "Phone is auto-resolved from WhatsApp context.";

    inputSchema = z.object({
        response: z
            .enum(["yes", "no", "n_a"])
            .describe("The staff's response: 'yes' = task done, 'no' = not done, 'n_a' = not applicable"),
        notes: z.string().optional().describe("Optional notes from the staff (e.g. reason for 'no')"),
        phone: z.string().optional().describe("Staff phone (auto-resolved from WhatsApp)"),
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    private resolvePhone(user: any, inputPhone?: string): string {
        return resolveStaffPhoneForByPhoneTools(
            {
                uid: (user as any)?.uid,
                data: (user as any)?.data,
                _luaProfile: (user as any)?._luaProfile,
            },
            inputPhone || null,
        );
    }

    private resolveToken(user: any, context?: any): string | undefined {
        return (
            env("LUA_WEBHOOK_API_KEY") ||
            env("WEBHOOK_API_KEY") ||
            env("MIZAN_SERVICE_TOKEN") ||
            (user as any)?.token ||
            (user as any)?.data?.token ||
            context?.metadata?.token ||
            context?.user?.data?.token ||
            context?.user?.token
        );
    }

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        const user = await User.get();
        const token = this.resolveToken(user, context);
        const phone = this.resolvePhone(user || context?.user, input.phone);

        if (!token) {
            return { status: "error", message: "Connection issue. Please try again." };
        }
        if (!phone) {
            return {
                status: "error",
                message: "I couldn't identify your account. Please try again.",
            };
        }

        try {
            console.log(`[ChecklistRespondTool] phone=${phone} response=${input.response}`);
            const result = await this.apiService.respondToChecklist(
                phone,
                input.response,
                token,
                input.notes,
            );
            console.log(`[ChecklistRespondTool] Response:`, JSON.stringify(result));

            if (!result.success) {
                return {
                    status: "error",
                    message:
                        result.message_for_user ||
                        result.error ||
                        "Could not record your response. Please try again.",
                };
            }

            const r = result as any;

            if (r.status === "completed") {
                const s = r.summary || {};
                return {
                    status: "completed",
                    message:
                        r.message_for_user ||
                        formatChecklistComplete({
                            yes: s.yes,
                            no: s.no,
                            n_a: s.n_a,
                            total: r.total,
                        }),
                    summary: r.summary,
                    instruction: "SEND this completion message to the staff. The checklist is finished.",
                };
            }

            if (r.status === "awaiting_photo") {
                const t = r.current_task || {};
                return {
                    status: "awaiting_photo",
                    answered: r.answered,
                    total: r.total,
                    current_task: t,
                    message:
                        r.message_for_user ||
                        formatPhotoAwaitPrompt({
                            title: t.title,
                            description: t.description,
                        }),
                    instruction:
                        "SEND this photo request. Wait for the staff to send an image. " +
                        "Do not call checklist_respond again until after the photo is received " +
                        "(the system will continue the checklist when they send the photo).",
                };
            }

            if (r.status === "next_task" && r.current_task) {
                const t = r.current_task;
                const task = {
                    index: t.index,
                    title: t.title,
                    description: t.description || "",
                    requires_photo: Boolean(t.requires_photo),
                };
                return {
                    status: "next_task",
                    answered: r.answered,
                    total: r.total,
                    current_task: { id: t.id, ...task },
                    message:
                        r.message_for_user ||
                        formatChecklistTaskPrompt(task, r.total, {
                            answered: r.answered,
                        }),
                    instruction: "SEND this next task to the staff and wait for their reply.",
                };
            }

            return { status: "error", message: "Unexpected response. Please try again." };
        } catch (error: any) {
            console.error("[ChecklistRespondTool] Error:", error.message);
            return {
                status: "error",
                message: "I couldn't record your response. Please try again.",
            };
        }
    }
}
