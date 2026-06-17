/**
 * Forces account_activation tool usage when staff send the WhatsApp invite prefilled message.
 * Specialists lack the supervisor's tenant-context preprocessor; this ensures phone + intent
 * are injected before the LLM can refuse or invent generic activation errors.
 */
import { PreProcessor } from "lua-cli";
import { resolveStaffPhoneForByPhoneTools, } from "../utils/resolveStaffPhoneFromLuaUser";
const ACTIVATION_PREFILL = "hi mizan ai, i am ready to activate my account!";
function isActivationMessage(text) {
    const lower = text.toLowerCase().trim();
    if (!lower)
        return false;
    if (lower.includes(ACTIVATION_PREFILL.replace("!", "")))
        return true;
    if (lower.includes("ready to activate"))
        return true;
    if (lower.includes("activate my account"))
        return true;
    if (lower.includes("accept invite") || lower.includes("accept invitation"))
        return true;
    if (lower.startsWith("hi mizan ai") && lower.includes("activate"))
        return true;
    return false;
}
function resolvePhone(user) {
    const u = user;
    return resolveStaffPhoneForByPhoneTools({
        uid: u.uid,
        data: u.data,
        _luaProfile: u._luaProfile,
    }, null);
}
export const accountActivationPreprocessor = new PreProcessor({
    name: "account-activation-router",
    description: "Detects staff account activation invite messages and injects phone + mandatory tool call hints.",
    priority: 10,
    execute: async (user, messages) => {
        const lastText = messages.filter((m) => m.type === "text").slice(-1)[0]?.text || "";
        if (!isActivationMessage(lastText)) {
            return { action: "proceed" };
        }
        const phone = resolvePhone(user);
        const phoneHint = phone
            ? `Pass phone: ${phone} to account_activation (or omit — tool resolves from WhatsApp uid).`
            : "Phone will be resolved from WhatsApp uid by the tool — still call account_activation NOW.";
        const block = `[CRITICAL: STAFF ACCOUNT ACTIVATION — NON-NEGOTIABLE]
The user sent the WhatsApp activation invite message. You MUST call account_activation in this turn.
${phoneHint}
DO NOT refuse. DO NOT ask for a PIN or app login. DO NOT say "there was an issue activating your account".
On success relay VERBATIM: "Congratulations! Your account has been successfully activated. Welcome to the team!"
On error relay the tool message field verbatim — never invent your own apology.`;
        console.log(`[AccountActivationPreprocessor] Activation detected; phone=${phone || "(from uid)"}`);
        const modifiedMessages = messages.map((m) => m.type === "text" ? { ...m, text: `${block}\n\n${m.text}` } : m);
        return { action: "proceed", modifiedMessage: modifiedMessages };
    },
});
export default accountActivationPreprocessor;
