/**
 * Runs account_activation in the preprocessor and blocks with the backend message
 * so the LLM cannot invent generic activation errors.
 */
import { ChatMessage, PreProcessor, UserDataInstance } from "lua-cli";
import AccountActivationTool from "../skills/tools/AccountActivationTool";
import {
    resolveStaffPhoneForByPhoneTools,
    type LuaUserPhoneSource,
} from "../utils/resolveStaffPhoneFromLuaUser";

const ACTIVATION_PREFILL =
    "hi mizan ai, i am ready to activate my account!";

const activationTool = new AccountActivationTool();

function isActivationMessage(text: string): boolean {
    const lower = text.toLowerCase().trim();
    if (!lower) return false;
    if (lower.includes(ACTIVATION_PREFILL.replace("!", ""))) return true;
    if (lower.includes("ready to activate")) return true;
    if (lower.includes("activate my account")) return true;
    if (lower.includes("accept invite") || lower.includes("accept invitation")) return true;
    if (lower.startsWith("hi mizan ai") && lower.includes("activate")) return true;
    return false;
}

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

export const accountActivationPreprocessor = new PreProcessor({
    name: "account-activation-router",
    description:
        "Detects staff activation invite messages, runs account_activation, and returns the backend message.",
    priority: 10,

    execute: async (user: UserDataInstance, messages: ChatMessage[], channel: string) => {
        const lastText =
            messages.filter((m) => m.type === "text").slice(-1)[0]?.text || "";
        if (!isActivationMessage(lastText)) {
            return { action: "proceed" as const };
        }

        const phone = resolvePhone(user);
        console.log(
            `[AccountActivationPreprocessor] Running account_activation; phone=${phone || "(from uid)"}, channel=${channel}`,
        );

        let toolResult: Record<string, unknown> = {};
        try {
            toolResult = (await activationTool.execute({ phone: phone || "" })) as Record<string, unknown>;
        } catch (err: unknown) {
            const em = err instanceof Error ? err.message : String(err);
            console.error("[AccountActivationPreprocessor] account_activation threw:", em);
            toolResult = {
                status: "error",
                message:
                    "We couldn't complete your activation right now. Please try again in a moment or contact your manager.",
            };
        }

        const message = String(toolResult.message || "").trim();
        if (message) {
            return {
                action: "block" as const,
                response: message,
                metadata: { activation_status: toolResult.status, activation_success: toolResult.status === "success" },
            };
        }

        return { action: "proceed" as const };
    },
});

export default accountActivationPreprocessor;
