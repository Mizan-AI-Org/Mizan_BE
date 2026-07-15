/**
 * ChecklistStarterTool
 *
 * Starts (or previews) the WhatsApp shift checklist via the Miya agent API.
 * Uses ShiftChecklistProgress — not the JWT checklist-template execution path.
 */

import { LuaTool, User, env } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { noContextError, upstreamError } from "./_common/errors";
import {
    formatChecklistComplete,
    formatChecklistStartIntro,
    formatChecklistTaskPrompt,
    formatPhotoAwaitPrompt,
} from "../../utils/checklistMessages";
import { resolveStaffPhoneForByPhoneTools } from "../../utils/resolveStaffPhoneFromLuaUser";

export default class ChecklistStarterTool implements LuaTool {
    name = "checklist_starter";
    description =
        "Start or preview the staff shift checklist on WhatsApp. " +
        "Use mode=start after clock-in or when staff say 'start checklist'. " +
        "Use mode=preview for 'what are my tasks' without starting. " +
        "Phone is auto-resolved from WhatsApp context.";

    inputSchema = z.object({
        mode: z
            .enum(["start", "preview"])
            .describe("'start' to begin the checklist flow, 'preview' to list tasks without starting"),
        trigger: z
            .enum(["clock_in", "manual"])
            .optional()
            .default("manual")
            .describe("What triggered the checklist start"),
        phone: z.string().optional().describe("Staff phone (auto-resolved from WhatsApp)"),
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
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

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        const user = await User.get();
        const token = this.resolveToken(user, context);
        const phone = this.resolvePhone(user || context?.user, input.phone);

        if (!token) {
            return noContextError({ hint: "No auth token available for checklist lookup." });
        }
        if (!phone || phone.length < 6) {
            return {
                status: "error",
                message: "I couldn't identify your account. Please try again.",
            };
        }

        try {
            if (input.mode === "preview") {
                const preview = await this.apiService.previewChecklistByPhone(phone, token);
                if (!preview.success) {
                    return {
                        status: "error",
                        message:
                            preview.message_for_user ||
                            preview.error ||
                            "I couldn't load your tasks right now.",
                    };
                }
                const items = preview.checklists || [];
                const total = preview.total_items || items.reduce((n, c) => n + (c.total_steps || 0), 0);
                if (!total) {
                    return {
                        status: "no_checklists",
                        message:
                            preview.message_for_user ||
                            "No checklist tasks on your shift right now — you're all set!",
                        next_action: "none",
                    };
                }
                const lines = items.flatMap((c) =>
                    (c.steps || []).map(
                        (s) => `• ${s.title}${s.requires_photo ? " (photo)" : ""}`,
                    ),
                );
                const clockHint = preview.clocked_in
                    ? "Say *start checklist* when you're ready and I'll walk you through them."
                    : "Clock in first, then say *start checklist* and I'll walk you through them.";
                return {
                    status: "ready",
                    clocked_in: preview.clocked_in,
                    total_items: total,
                    message:
                        preview.message_for_user ||
                        `You've got ${total} task${total === 1 ? "" : "s"} on this shift:\n${lines.slice(0, 12).join("\n")}${lines.length > 12 ? "\n…" : ""}\n\n${clockHint}`,
                    next_action: "prompt_user",
                };
            }

            const result: any = await this.apiService.startWhatsAppChecklistByPhone(phone, token);

            if (!result.success) {
                return {
                    status: result.clocked_in === false ? "not_clocked_in" : "error",
                    clocked_in: result.clocked_in,
                    message:
                        result.message_for_user ||
                        result.error ||
                        "I couldn't start your checklist. Please try again.",
                };
            }

            if (result.status === "completed") {
                return {
                    status: "completed",
                    message:
                        result.message_for_user ||
                        formatChecklistComplete({ total: result.total }),
                    instruction: "SEND this message to the staff. Checklist already finished.",
                };
            }

            const t = result.current_task;
            const total = result.total || (result.tasks || []).length || 1;
            if (!t) {
                return {
                    status: "no_checklists",
                    message:
                        result.message_for_user ||
                        "No tasks are assigned to your shift right now. You're all set!",
                };
            }

            if (result.status === "awaiting_photo") {
                return {
                    status: "awaiting_photo",
                    total,
                    current_task: t,
                    message:
                        result.message_for_user ||
                        formatPhotoAwaitPrompt({
                            title: t.title,
                            description: t.description,
                        }),
                    instruction:
                        "SEND this photo request and wait for the staff to send an image.",
                    next_action: "await_photo",
                };
            }

            const task = {
                index: t.index || 1,
                title: t.title || "Task",
                description: t.description || "",
                requires_photo: Boolean(t.requires_photo),
            };
            const isResume = result.status === "in_progress";
            const message = isResume
                ? formatChecklistTaskPrompt(task, total, {
                      isFirst: task.index === 1,
                      answered: Math.max(0, task.index - 1),
                  })
                : formatChecklistStartIntro(total, task);

            return {
                status: isResume ? "in_progress" : "started",
                total,
                current_task: { id: t.id, ...task },
                message: result.message_for_user || message,
                instruction: "SEND this message to the staff and wait for Yes / No / N/A.",
                next_action: "present_step",
            };
        } catch (error: any) {
            console.error("[ChecklistStarterTool] Error:", error.message);
            return upstreamError(error.message);
        }
    }
}
