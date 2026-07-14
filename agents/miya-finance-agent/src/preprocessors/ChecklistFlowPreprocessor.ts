/**
 * Deterministic checklist start + Yes/No/N/A replies so the LLM cannot skip tools
 * or invent robotic copy. Mirrors ClockInPreprocessor.
 */
import { ChatMessage, PreProcessor, UserDataInstance } from "lua-cli";
import ChecklistRespondTool from "../skills/tools/ChecklistRespondTool";
import ChecklistStarterTool from "../skills/tools/ChecklistStarterTool";
import { extractLastUserText } from "../utils/extractLastUserText";
import {
    resolveStaffPhoneForByPhoneTools,
    type LuaUserPhoneSource,
} from "../utils/resolveStaffPhoneFromLuaUser";
import {
    isPreviewTasksMessage,
    isStartChecklistMessage,
    isWhatShouldIDoNextAsk,
} from "../shared/checklistIntent";

const respondTool = new ChecklistRespondTool();
const starterTool = new ChecklistStarterTool();

function resolvePhone(user: UserDataInstance): string {
    const u = user as unknown as LuaUserPhoneSource & { uid?: string };
    return resolveStaffPhoneForByPhoneTools(
        {
            uid: u.uid,
            data: (u as { data?: Record<string, unknown> }).data,
            _luaProfile: (u as { _luaProfile?: Record<string, unknown> })._luaProfile,
        },
        null,
    );
}

function parseChecklistResponse(text: string): "yes" | "no" | "n_a" | null {
    const t = text.trim().toLowerCase().replace(/\s+/g, " ");
    if (!t) return null;
    // Button payloads / short answers only — avoid hijacking longer messages
    if (/^(yes|y|oui|نعم|اه|أيوه|ايوه|✅)$/i.test(t)) return "yes";
    if (/^(no|n|non|لا|❌)$/i.test(t)) return "no";
    if (/^(n\/?a|n a|na|skip|➖|غير\s*معني|pas\s*applicable)$/i.test(t)) return "n_a";
    return null;
}

function isStartChecklist(text: string): boolean {
    return isStartChecklistMessage(text);
}

function isPreviewChecklist(text: string): boolean {
    return isPreviewTasksMessage(text);
}

export const checklistFlowPreprocessor = new PreProcessor({
    name: "checklist-flow-router",
    description:
        "Starts checklists and records Yes/No/N/A replies deterministically for natural WhatsApp flow.",
    // Ahead of LLM so typo'd "tasks today" / "start checklists" never invent tech errors
    priority: 90,

    execute: async (user: UserDataInstance, messages: ChatMessage[], channel: string) => {
        const lastText = extractLastUserText(messages);
        const phone = resolvePhone(user);
        const response = parseChecklistResponse(lastText);

        if (response) {
            console.log(
                `[ChecklistFlowPreprocessor] respond=${response} phone=${phone || "(uid)"} channel=${channel}`,
            );
            let toolResult: Record<string, unknown> = {};
            try {
                toolResult = (await respondTool.execute({
                    response,
                    phone: phone || undefined,
                })) as Record<string, unknown>;
            } catch (err: unknown) {
                const em = err instanceof Error ? err.message : String(err);
                console.error("[ChecklistFlowPreprocessor] checklist_respond threw:", em);
                toolResult = {
                    status: "error",
                    message: "I couldn't record that just now. Please reply Yes, No, or N/A again.",
                };
            }
            const status = String(toolResult.status || "");
            const message = String(toolResult.message || "").trim();
            // If there's no active checklist, let the LLM handle a casual "yes"/"no"
            if (
                status === "error" &&
                /no active checklist|don't have an active checklist|start checklist/i.test(message)
            ) {
                return { action: "proceed" as const };
            }
            if (message) {
                return {
                    action: "block" as const,
                    response: message,
                    metadata: {
                        checklist_status: toolResult.status,
                        checklist_response: response,
                    },
                };
            }
            return { action: "proceed" as const };
        }

        // Staff companion: "what should I do next?" → preview tasks (never invent empty coaching).
        const whatNext = isWhatShouldIDoNextAsk(lastText);
        if (isStartChecklist(lastText) || isPreviewChecklist(lastText) || whatNext) {
            const mode =
                whatNext || (isPreviewChecklist(lastText) && !isStartChecklist(lastText))
                    ? "preview"
                    : "start";
            console.log(
                `[ChecklistFlowPreprocessor] starter mode=${mode} whatNext=${whatNext} phone=${phone || "(uid)"} channel=${channel}`,
            );
            let toolResult: Record<string, unknown> = {};
            try {
                toolResult = (await starterTool.execute({
                    mode,
                    trigger: "manual",
                    phone: phone || undefined,
                })) as Record<string, unknown>;
            } catch (err: unknown) {
                const em = err instanceof Error ? err.message : String(err);
                console.error("[ChecklistFlowPreprocessor] checklist_starter threw:", em);
                toolResult = {
                    status: "error",
                    message: "I couldn't load your checklist right now. Please try again in a moment.",
                };
            }
            const message = String(toolResult.message || "").trim();
            let response =
                message ||
                (mode === "preview"
                    ? "I couldn't load your tasks right now. Please try again in a moment."
                    : "I couldn't start your checklist right now. Make sure you're clocked in, then say *start checklist* again.");
            if (
                whatNext &&
                message &&
                !/clock/i.test(message) &&
                /no (active )?checklist|no tasks|nothing to do|0 tasks/i.test(message)
            ) {
                response =
                    `${message}\n\n` +
                    `If you're on shift, say *Clock me in* then *Start checklist*. ` +
                    `Or ask *When is my shift today?*`;
            } else if (whatNext && message) {
                response =
                    `Here's what to do next:\n\n${message}\n\n` +
                    `Reply *Start checklist* when you're ready, or *Yes* / *No* / *N/A* on each step.`;
            }
            return {
                action: "block" as const,
                response,
                metadata: {
                    checklist_status: toolResult.status,
                    checklist_mode: mode,
                    staff_companion_what_next: whatNext,
                },
            };
        }

        return { action: "proceed" as const };
    },
});

export default checklistFlowPreprocessor;
