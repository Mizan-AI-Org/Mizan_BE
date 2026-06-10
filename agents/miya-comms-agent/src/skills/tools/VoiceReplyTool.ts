/**
 * Voice reply tool — Miya speaks her reply as a WhatsApp voice note.
 *
 * Use cases:
 *  - Manager prefers voice replies (set in their preferences/profile).
 *  - Staff sent Miya a voice note and a voice reply feels more natural
 *    than text.
 *  - Long replies on the road where reading would be unsafe (e.g. an
 *    invoice summary while driving between branches).
 *
 * Implementation: backend renders the text with OpenAI TTS (`tts-1`),
 * uploads the mp3 to WhatsApp Cloud as a media object, then sends an
 * `audio` message with `voice=true` so it shows up as a push-to-talk
 * bubble. If TTS or WhatsApp upload fails, the tool returns an error
 * so Miya can fall back to a regular text reply.
 *
 * Best practice: keep replies short (< ~30s spoken). The backend caps
 * the TTS input at 1500 chars but anything beyond ~250 chars starts to
 * feel like a voicemail; trim to the essential point before calling
 * this tool.
 */
import { LuaTool, User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { noContextError } from "./_common/errors";

function getRestaurantId(user: any) {
    const userData = user?.data || {};
    const profile = (user as any)?._luaProfile || {};
    return (
        (user as any)?.restaurantId ||
        userData.restaurantId ||
        profile.restaurantId ||
        (profile.metadata && (profile.metadata as any).restaurantId)
    );
}

function getUserPhone(user: any) {
    const userData = user?.data || {};
    return (
        (user as any)?.phone ||
        userData.phone ||
        userData.phoneNumber ||
        userData.whatsappPhone ||
        null
    );
}

const _voiceApi = new ApiService();


export class VoiceReplyTool implements LuaTool {
    name = "voice_reply";
    description =
        "Send Miya's reply as a WhatsApp voice note (TTS). Use when (a) the user explicitly asks for a voice reply, (b) the user just sent a voice note (mirror their channel), or (c) the user's profile says they prefer voice. DO NOT use for routine text replies - voice notes are slower to consume and harder to skim. Keep `text` short and conversational, ideally under 250 characters.";

    inputSchema = z.object({
        text: z
            .string()
            .min(1)
            .describe(
                "What Miya should say out loud. Plain conversational sentences. Hard cap is 1500 chars; aim for under 250.",
            ),
        phone: z
            .string()
            .optional()
            .describe(
                "Recipient phone (E.164, e.g. +212600000000). Defaults to the current user's WhatsApp number from context.",
            ),
        caption: z
            .string()
            .optional()
            .describe(
                "Optional follow-up text bubble sent right after the voice note. Use for actionable links or buttons that won't fit in audio (e.g. 'Tap here to approve: <link>').",
            ),
        voice: z
            .enum(["alloy", "echo", "fable", "onyx", "nova", "shimmer"])
            .optional()
            .describe("OpenAI TTS voice id. Defaults to 'alloy' (warm, neutral)."),
        speed: z
            .number()
            .min(0.25)
            .max(4.0)
            .optional()
            .describe("Speech rate. 1.0 = normal. Use 1.05-1.1 for snappier replies."),
        voiceNote: z
            .boolean()
            .optional()
            .describe(
                "Render as push-to-talk bubble (default true). Set false for a regular audio attachment.",
            ),
        restaurantId: z
            .string()
            .optional()
            .describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]."),
    });

    constructor(private apiService: ApiService = _voiceApi) {}

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get();
        if (!user) return { status: "error", message: "No context." };
        const rid = input.restaurantId || getRestaurantId(user);
        if (!rid) return noContextError();

        const phone = input.phone || getUserPhone(user);
        if (!phone) {
            return {
                status: "error",
                message:
                    "No recipient phone available. Pass `phone` explicitly or have the user link their WhatsApp number first.",
            };
        }

        const data = await this.apiService.sendVoiceReply(rid, {
            phone,
            text: input.text,
            caption: input.caption,
            voice: input.voice,
            speed: input.speed,
            voiceNote: input.voiceNote,
        });

        if (!data || data.success === false || data.delivered === false) {
            return {
                status: "error",
                // Surface the backend's user-facing fallback hint when present.
                message:
                    data?.message_for_user ||
                    data?.error ||
                    "Voice reply failed; fall back to a text reply.",
                fallback: "text",
            };
        }

        return {
            status: "success",
            // Critical: tell the orchestrator NOT to also send a text
            // duplicate of `input.text` -- we already shipped it as audio.
            already_sent: true,
            message_for_user: data.message_for_user || "Voice note sent.",
            media_id: data.media_id,
            message_id: data.message_id,
            bytes: data.bytes,
        };
    }
}
