import { LuaTool, User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { IncidentManagementModule } from "../../modules/incident-management";
import { resolveAgentContext } from "../../services/agentContext";

type IncidentLang = "en" | "fr" | "ar";

function resolveIncidentLanguage(context?: any): IncidentLang {
    const raw =
        context?.metadata?.language ??
        context?.metadata?.locale ??
        context?.language ??
        context?.user?.language ??
        "";
    const s = String(raw).toLowerCase().split(/[-_]/)[0] ?? "";
    if (s === "fr") return "fr";
    if (s === "ar") return "ar";
    return "en";
}

function excerptForUser(text: string, max = 240): string {
    const t = text.replace(/\s+/g, " ").trim();
    if (t.length <= max) return t;
    return `${t.slice(0, max - 1).trimEnd()}…`;
}

/**
 * Warm, comprehensive confirmation for WhatsApp — no ticket IDs, categories, or priority labels.
 */
function buildHumanIncidentConfirmation(opts: {
    lang: IncidentLang;
    staffName: string;
    description: string;
    category: string;
}): string {
    const name = (opts.staffName || "").trim() || "there";
    const snippet = excerptForUser(opts.description, 260);
    const cat = (opts.category || "General").trim();

    if (opts.lang === "fr") {
        if (cat === "HR") {
            return (
                `Merci ${name} d’avoir pris le temps de nous écrire — nous avons bien enregistré ce que vous avez partagé et les personnes concernées en direction ont été informées, en respectant la confidentialité.\n\n` +
                `Vous nous avez indiqué : « ${snippet} »\n\n` +
                `Nous prenons ce type de situation au sérieux. Quelqu’un reviendra vers vous avec attention. Si vous avez besoin d’aide immédiatement ou si vous ne vous sentez pas en sécurité, parlez-en tout de suite à un manager présent ou à votre contact RH.`
            );
        }
        if (cat === "Safety") {
            return (
                `Merci ${name} — nous avons bien reçu votre signalement et l’équipe de direction a été prévenue pour réagir rapidement.\n\n` +
                `Vous nous avez signalé : « ${snippet} »\n\n` +
                `Nous allons nous assurer que la zone soit vérifiée et que tout le monde reste en sécurité. Si la situation empire ou si quelqu’un est blessé, prévenez immédiatement un manager sur place.`
            );
        }
        if (cat === "Maintenance") {
            return (
                `Merci ${name} — votre message est enregistré et a été transmis à l’équipe pour qu’on puisse corriger le problème.\n\n` +
                `Détail signalé : « ${snippet} »\n\n` +
                `La maintenance ou la direction suivra selon l’urgence. Si c’est dangereux ou que ça bloque le service, dites-le aussi à un manager sur place.`
            );
        }
        if (cat === "Service") {
            return (
                `Merci ${name} — nous avons bien noté votre retour et nous l’avons partagé avec l’équipe pour améliorer l’expérience des invités.\n\n` +
                `Vous avez indiqué : « ${snippet} »\n\n` +
                `Quelqu’un pourra revenir vers vous ou en discuter en équipe selon ce qui est nécessaire. Merci encore pour votre franchise.`
            );
        }
        return (
            `Merci ${name} — nous avons bien enregistré votre message et l’avons transmis aux bonnes personnes.\n\n` +
            `Résumé : « ${snippet} »\n\n` +
            `L’équipe en tiendra compte et fera le suivi si besoin. Pour toute urgence, parlez-en aussi à un manager sur place.`
        );
    }

    if (opts.lang === "ar") {
        if (cat === "HR") {
            return (
                `شكرًا ${name} على ثقتك — سجّلنا ما شاركته معنا وأبلغنا الإدارة المناسبة بسرية تامة.\n\n` +
                `ما أبلغتنا به: « ${snippet} »\n\n` +
                `نأخذ مثل هذه الأمور بجدية. سيتواصل معك الفريق المعني بكل عناية. إذا احتجت مساعدة فورية أو لم تشعر بالأمان، تواصل فورًا مع المسؤول الموجود أو مع جهة الموارد البشرية.`
            );
        }
        if (cat === "Safety") {
            return (
                `شكرًا ${name} — استلمنا بلاغك وأبلغنا الإدارة للتحرك بسرعة.\n\n` +
                `ما وصفته: « ${snippet} »\n\n` +
                `سنتأكد أن المنطقة تُفحص وأن الجميع يبقون بأمان. إذا تفاقم الوضع أو وُجد إصابة، أبلغ المسؤول فورًا.`
            );
        }
        if (cat === "Maintenance") {
            return (
                `شكرًا ${name} — سجّلنا المشكلة وشاركناها مع الفريق لمعالجتها.\n\n` +
                `التفاصيل: « ${snippet} »\n\n` +
                `ستتابع الصيانة أو الإدارة حسب الأولوية. إذا كان الأمر خطيرًا أو يعطل العمل، أخبر مسؤولًا في المكان أيضًا.`
            );
        }
        if (cat === "Service") {
            return (
                `شكرًا ${name} — أخذنا ملاحظتك بعين الاعتبار وشاركناها مع الفريق لتحسين تجربة الضيوف.\n\n` +
                `ما ذكرته: « ${snippet} »\n\n` +
                `يمكن أن يتم المتابعة معك أو داخل الفريق حسب الحاجة. نقدّر صراحتك.`
            );
        }
        return (
            `شكرًا ${name} — سجّلنا رسالتك وأرسلناها للجهات المناسبة.\n\n` +
            `ملخص: « ${snippet} »\n\n` +
            `ستأخذها الإدارة في الاعتبار. لأي طارئ، تواصل مع المسؤول في المكان.`
        );
    }

    // English (default)
    if (cat === "HR") {
        return (
            `Hi ${name}, thank you for trusting us with this. What you shared has been recorded and passed to the right people in management, with confidentiality in mind.\n\n` +
            `You let us know: "${snippet}"\n\n` +
            `We take reports like this seriously. Someone will follow up with care. If you need help right away or don’t feel safe at work, please reach out to a manager on duty or your HR contact immediately—you’re not alone, and support is available.`
        );
    }
    if (cat === "Safety") {
        return (
            `Hi ${name}, thank you for speaking up—we’ve logged your report and notified management so they can respond quickly.\n\n` +
            `You reported: "${snippet}"\n\n` +
            `We’ll make sure the situation is checked and that everyone stays safe. If anything gets worse or someone is hurt, tell a manager on duty right away.`
        );
    }
    if (cat === "Maintenance") {
        return (
            `Hi ${name}, thanks for the heads-up—we’ve recorded it and shared it with the team so it can be fixed.\n\n` +
            `Details: "${snippet}"\n\n` +
            `Maintenance or management will follow up based on urgency. If it’s unsafe or blocking service, please also flag it to a manager on the floor.`
        );
    }
    if (cat === "Service") {
        return (
            `Hi ${name}, thank you for the feedback—we’ve captured what you shared and passed it along so the team can improve the guest experience.\n\n` +
            `You mentioned: "${snippet}"\n\n` +
            `Someone may follow up with you or address it with the team as needed. We really appreciate you saying something.`
        );
    }
    return (
        `Hi ${name}, thank you for reaching out—we’ve logged your message and shared it with the right people.\n\n` +
        `Summary: "${snippet}"\n\n` +
        `The team will take it from here. If anything feels urgent, please also speak with a manager on duty.`
    );
}

export default class IncidentReportTool implements LuaTool {
    name = "report_incident";
    description =
        "Report an incident or issue occurring in the restaurant (e.g. equipment failure, safety issue, broken glass, service problem). " +
        "Use when a staff member describes a problem via text, voice, or photo—in English, Arabic, or French. " +
        "Pass the core issue as 'description'. For photo with caption: pass the caption. For photo without caption: pass 'Incident reported with photo'. " +
        "For voice message: pass the transcript or 'Incident reported via voice'. " +
        "ALWAYS pass the phone from context (User Phone). Restaurant is auto-resolved from phone if not in context.";

    inputSchema = z.object({
        description: z.string().optional().describe(
            "The core issue or incident—what the staff reported. Can be from text, voice transcript, or photo caption. " +
            "If the staff sent only a photo with no caption, pass 'Incident reported with photo'. " +
            "If the staff sent a voice note, pass the transcript or 'Incident reported via voice'."
        ),
        restaurantId: z.string().optional().describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]. Falls back to phone-based lookup if not provided."),
        phone: z.string().optional().describe("Staff phone from context (User Phone). ALWAYS pass this for reliable incident reporting."),
        source: z.enum(["text", "voice", "photo"]).optional().describe("How the incident was reported: text, voice, or photo"),
    });

    private apiService: ApiService;
    private incidentModule: IncidentManagementModule;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
        this.incidentModule = new IncidentManagementModule();
    }

    private resolvePhone(user: any, inputPhone?: string, ctxPhone?: string, context?: any): string | undefined {
        if (inputPhone) {
            const digits = String(inputPhone).replace(/\D/g, "");
            if (digits.length >= 6) return digits;
        }
        if (ctxPhone) return ctxPhone;

        const userData = user ? ((user as any).data || {}) : {};
        const profile = user ? ((user as any)._luaProfile || {}) : {};
        const metadata = profile.metadata && typeof profile.metadata === "object" ? profile.metadata : {};

        const candidates = [
            context?.user?.phone,
            context?.metadata?.phone,
            context?.user?.data?.phone,
            context?.channel?.phone,
            context?.metadata?.details?.phone,
            context?.event?.from,
            context?.message?.from,
            (context?.metadata?.details as any)?.phone,
            userData.phone,
            (metadata as any)?.phone,
            profile.phoneNumber,
            profile.mobileNumber,
        ];

        let phone = candidates.find((p) => p && String(p).replace(/\D/g, "").length >= 6);
        if (phone) return String(phone).replace(/\D/g, "");

        const uid = (user as any)?.uid ? String((user as any).uid) : "";
        if (uid) {
            const afterColon = uid.includes(":") ? uid.split(":").slice(1).join(":").trim() : uid;
            const digits = (afterColon || uid).replace(/\D/g, "");
            if (digits.length >= 6) return digits;
        }

        return undefined;
    }

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        const ctx = await resolveAgentContext(input.restaurantId);
        const user = await User.get();
        const userData = user ? ((user as any).data || {}) : {};

        let reporterPhone = this.resolvePhone(user, input.phone, ctx.phone, context);
        let restaurantId = ctx.restaurantId;
        let staffName = context?.user?.name || userData.first_name || userData.userName || "Staff";

        console.log(`[IncidentReportTool] phone=${reporterPhone || "none"} restaurantId=${restaurantId || "none"} source=${input.source || "unknown"} inputPhone=${input.phone || "none"}`);

        // Guard rail: detect routine repair requests that should be MAINTENANCE staff_requests, not safety incidents.
        // This prevents Miya from dumping "fridge needs repair" into the incident inbox just because the input
        // contained the word "broken". If we see clear repair language WITHOUT any danger keyword, refuse and
        // redirect to the right tool — the agent will retry with staff_request(category='MAINTENANCE').
        //
        // IMPORTANT: "broken glass" / shards / wet floor / slips are SAFETY hazards, not equipment repairs.
        const desc0 = String(input.description || "").toLowerCase().trim();
        if (desc0.length >= 3) {
            const SAFETY_HAZARD =
                /\b(broken\s+glass|glass\s+(on|at|by|near|under|broken|shatter)|shatter(?:ed)?\s+glass|shard|shards|verre\s+cass[eé]?|bris\s+de\s+verre|verre\s+bris[eé]?|wet\s+floor|spill(?:ed)?|hazard|sharp\s+(?:glass|edge|object)|table\s+\d+.{0,40}(?:glass|spill|slip)|slip(?:ped)?|fall(?:en)?|fell|injur|hurt|bleed|burn|fire|smoke|gas\s+(?:leak|smell)|food\s+poison|harass|theft|robbery|unconscious)\b/;
            const REPAIR =
                /\b(repair|repairs|repaired|fix|fixed|fixing|broken|down|out of order|not working|stopped working|en panne|hs|à réparer|a reparer|réparer|reparer|cassé|casse|en rade|kharbana|khasser|mkhsser|إصلاح|تصليح|عطل|خربان)\b/;
            const DANGER =
                /\b(fire|smoke|flame|gas|gaz|leak.*flooding|flooding|spark|electric shock|shock|injur|hurt|bleed|burn|burned|burnt|slip|slipped|fall|fell|food poisoning|spoiled food|robbery|robbed|theft|stolen|harass|attack|fight|emergency|évanoui|inconscient|unconscious|incendie|fumée|étincelle|électrocut|blessé|brûl|saign|chute|tomb|empoisonn|vol|harcèl|حريق|دخان|إصاب|جرح|نزيف|حرق|انزلاق|تسمم|سرقة|تحرش|طوارئ|glass|shard|verre|bris)\b/;
            if (!SAFETY_HAZARD.test(desc0) && REPAIR.test(desc0) && !DANGER.test(desc0)) {
                console.warn(`[IncidentReportTool] Repair language detected without danger keywords — redirecting to MAINTENANCE staff_request: "${desc0.substring(0, 80)}"`);
                return {
                    status: "wrong_tool",
                    code: "USE_MAINTENANCE_STAFF_REQUEST",
                    message:
                        "This looks like a routine equipment repair, not a safety incident. Please call staff_request " +
                        "with category='MAINTENANCE', subject=<short issue>, description=<the same description>, " +
                        "priority='HIGH' if the user said 'super soon / ASAP / urgent'. Do NOT call report_incident " +
                        "for routine repairs — that inbox is reserved for active safety/legal events (fire, gas, injury, theft, harassment, broken glass).",
                    miya_directive:
                        "Re-route this clause NOW in the same turn: call staff_request(category='MAINTENANCE') with the SAME description. " +
                        "Then continue handling the user's other intents.",
                };
            }
        }

        // Phone lookup fallback for restaurant and staff name
        if ((!restaurantId || !staffName || staffName === "Staff") && reporterPhone) {
            console.log(`[IncidentReportTool] Looking up staff by phone: ${reporterPhone}`);
            try {
                const staffLookup = await this.apiService.getStaffByPhoneForAgent(reporterPhone);
                if (staffLookup.success && staffLookup.found && staffLookup.staff) {
                    if (!restaurantId) restaurantId = staffLookup.staff.restaurant_id || undefined;
                    if (!staffName || staffName === "Staff") staffName = staffLookup.staff.first_name || staffName;
                    console.log(`[IncidentReportTool] Resolved: ${staffName} @ restaurant ${restaurantId}`);
                } else {
                    console.warn(`[IncidentReportTool] Staff lookup: ${staffLookup.error || "Not found"}`);
                }
            } catch (lookupErr: any) {
                console.error(`[IncidentReportTool] Phone lookup error: ${lookupErr.message}`);
            }
        }

        if (!restaurantId) {
            console.error(`[IncidentReportTool] No restaurant resolved. phone=${reporterPhone || "none"}, inputPhone=${input.phone || "none"}`);
            // If the caller is a manager via the dashboard (restaurant + Mizan user id, no WhatsApp phone), redirect to staff_request.
            const looksLikeManagerWidget = Boolean(ctx.restaurantId && ctx.userId && !reporterPhone);
            if (looksLikeManagerWidget) {
                return {
                    status: "wrong_tool",
                    code: "USE_STAFF_REQUEST_FOR_MANAGER",
                    message:
                        "Managers using the dashboard widget should not file safety incidents through report_incident. " +
                        "Please call staff_request with the appropriate category (MAINTENANCE for equipment, OTHER for general issues).",
                    miya_directive:
                        "Retry with staff_request(category='MAINTENANCE' if equipment, otherwise 'OTHER', subject, description). " +
                        "Always pass restaurantId from [SYSTEM: PERSISTENT CONTEXT].",
                };
            }
            return {
                status: "error",
                code: "NO_TENANT_CONTEXT",
                message: "I couldn't link this report to a restaurant. Please make sure you're messaging from the phone number we have on file for your staff account.",
            };
        }

        try {
            const desc =
                (input.description ?? "").trim() ||
                (context?.message?.body && String(context.message.body).trim()) ||
                (context?.lastMessage?.text && String(context.lastMessage.text).trim()) ||
                (context?.metadata?.lastUserMessage && String(context.metadata.lastUserMessage).trim()) ||
                "";
            const source = input.source || "text";
            const description = desc.length >= 2
                ? desc
                : source === "photo"
                    ? "Incident reported with photo (no caption provided)."
                    : source === "voice"
                        ? "Incident reported via voice note (transcript unavailable)."
                        : "Incident reported (no additional details provided).";

            console.log(`[IncidentReportTool] Analyzing: "${description.substring(0, 60)}..." for restaurant ${restaurantId}`);

            let analysis: { issueDescription: string; category: string; priority: string; suggestedAction: string };
            try {
                const analysisResult = await this.incidentModule.analyzeIncident(description, {
                    restaurantId,
                    staffName,
                    source,
                });
                analysis = analysisResult.analysis;
            } catch (analysisErr: any) {
                console.warn("[IncidentReportTool] Analysis failed, using defaults:", analysisErr.message);
                analysis = {
                    issueDescription: description.substring(0, 500),
                    category: "General",
                    priority: "MEDIUM",
                    suggestedAction: "Review details.",
                };
            }

            const issueDescription = analysis.issueDescription || description;
            const title = `${analysis.category} incident`;

            console.log(`[IncidentReportTool] Submitting: ${analysis.priority} ${analysis.category}`);

            const result = await this.apiService.createIncidentReportForAgent({
                restaurant_id: restaurantId,
                title,
                description: issueDescription,
                category: analysis.category,
                priority: analysis.priority,
                reporter_phone: reporterPhone,
            });

            if (result.success === false || result.error) {
                const userFacing =
                    (result as any).message_for_user ||
                    (typeof result.error === "string" && result.error.includes("restaurant")
                        ? "We couldn't link this report to your restaurant. Please make sure you're messaging from the phone number we have on file."
                        : result.error);
                return { status: "error", message: userFacing };
            }

            const lang = resolveIncidentLanguage(context);
            const userMessage = buildHumanIncidentConfirmation({
                lang,
                staffName,
                description: issueDescription,
                category: analysis.category,
            });

            // NOTE: We deliberately do NOT expose ticket IDs, category, priority, or any
            // structured metadata back to the LLM. Earlier versions did, and the model
            // would dutifully paste them into the reply as "Ticket: #abc12345 / Type: Service
            // / Priority: MEDIUM" — which is exactly the cold, help-desk tone we want to
            // avoid when a staff member is reporting a safety or HR incident. The backend
            // still has the full ticket (logs, ops dashboard, assignee notification); the
            // staff-facing reply is just the warm human message.
            return {
                status: "success",
                message: userMessage,
                userMessage,
                instruction:
                    "Send the userMessage above to the staff member VERBATIM. Do NOT add a " +
                    "checkmark, a 'Ticket: #...' line, a 'Type:' line, a 'Priority:' line, or " +
                    "any summary. The userMessage is the complete reply in the staff's language.",
            };
        } catch (error: any) {
            console.error("[IncidentReportTool] Execution failed:", error.message);
            const errMsg =
                error.response?.data?.error ||
                error.response?.data?.message_for_user ||
                error.message;
            return {
                status: "error",
                message:
                    typeof errMsg === "string" && errMsg.length > 0
                        ? errMsg
                        : "Something went wrong while reporting the incident. Please try again or contact your manager.",
            };
        }
    }
}
