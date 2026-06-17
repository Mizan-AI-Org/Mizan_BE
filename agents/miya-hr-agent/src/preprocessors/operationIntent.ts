/**
 * Deterministic intent detection for high-frequency WhatsApp operational commands.
 * Used by OperationsCommandPreprocessor so Miya executes tools instead of hallucinating.
 */

export type OperationCommandIntent =
    | { kind: "lookup"; query: string }
    | { kind: "personal_ops_reminder"; title: string; description?: string }
    | {
          kind: "dashboard_reminder";
          title: string;
          description?: string;
          category: "HR" | "PAYROLL" | "OPERATIONS" | "FINANCE" | "MEETING";
          widgetId?: string;
      }
    | {
          kind: "purchase_order";
          subject: string;
          description: string;
          assigneeName?: string;
          priority?: "LOW" | "MEDIUM" | "HIGH" | "URGENT";
      }
    | {
          kind: "maintenance";
          subject: string;
          description: string;
          priority?: "LOW" | "MEDIUM" | "HIGH" | "URGENT";
      }
    | {
          kind: "record_invoice";
          vendor: string;
          amount: number;
          dueDate: string;
          invoiceNumber?: string;
          notes?: string;
          currency?: string;
          paymentMethod?: string;
      }
    | { kind: "chase"; query: string }
    | {
          kind: "generate_payslip";
          staffName?: string;
          month?: number;
          year?: number;
          periodStart?: string;
          periodEnd?: string;
      }
    | { kind: "temperature_log"; text: string; equipment?: string; valueC?: number }
    | {
          kind: "bank_payment_status";
          vendor?: string;
          invoiceNumber?: string;
          status: string;
          reference?: string;
          note?: string;
      }
    | { kind: "delivery_menu_sync"; provider?: string }
    | { kind: "seed_compliance" };

const LOOKUP_ID_RE =
    /(?:num[eé]ro\s+(?:de\s+)?demande|request\s*(?:#|n[o°]\.?)?|demande\s*(?:#|n[o°]\.?)?|task\s*#?|#)\s*([a-f0-9]{6,12})/i;

const LOOKUP_PHRASE_RE =
    /\b(tu\s+l['']?(?:as|a)\s+enregistr[eé]|o[uù]\s+(?:est|as-tu|l['']?as)|where\s+did\s+you\s+(?:save|record|log)|find\s+(?:this|that|the)\s+(?:request|operation|task)|je\s+trouve\s+pas|can['']?t\s+find\s+(?:this|that|the))\b/i;

const CHASE_RE =
    /\b(?:follow\s*up\s+(?:with|on|about|for)|relance(?:r)?(?:\s+(?:sur|with|on|de|la|le))?|chase|check\s+in\s+(?:with|on)|rappeler?\s+(?:.+?\s+)?(?:sur|about|on|for))\b/i;

const PAYSLIP_GENERATE_RE =
    /\b(?:generate|g[eé]n[eé]rer|create|cr[eé]er|prepare|pr[eé]parer)\s+(?:the\s+)?(?:staff\s+)?(?:payslips?|fiches?\s+de\s+paie|bulletins?\s+de\s+paie)\b/i;

const TEMPERATURE_LOG_RE =
    /\b(?:\d+(?:[.,]\d+)?\s*(?:°?\s*[cC]|degrees?\s*c|degr[eé]s?\s*c)|(?:walk[- ]?in|fridge|freezer|cooler|chambre\s+froide|cong[eé]lateur|hot\s+holding).{0,30}\d+(?:[.,]\d+)?\s*(?:°?\s*[cC]|degr[eé]s?\s*c))\b/i;

const BANK_PAYMENT_STATUS_RE =
    /\b(?:bank\s+transfer\s+(?:sent|initiated|ordered)|transfer\s+(?:sent|initiated|ordered)|virement\s+(?:envoy[eé]|fait|effectu[eé])|cheque\s+(?:written|issued|[eé]mis)|payment\s+(?:sent|initiated)|mark\s+(?:transfer|payment)\s+(?:as\s+)?(?:sent|initiated|cleared))\b/i;

const DELIVERY_MENU_SYNC_RE =
    /\b(?:sync\s+(?:the\s+)?(?:delivery|glovo|uber\s+eats)\s+menu|push\s+menu\s+to\s+glovo|mettre\s+[àa]\s+jour\s+(?:le\s+)?menu\s+(?:glovo|livraison))\b/i;

const COMPLIANCE_SEED_RE =
    /\b(?:cnss|tax\s+calendar|compliance\s+calendar|calendrier\s+(?:cnss|fiscal|compliance)|seed\s+compliance)\b/i;

const PERSONAL_OPS_RE =
    /\b(rappel\s+personnel|personal\s+reminder|place\s+(?:le|la|it|this)\s+(?:dans\s+)?op[eé]rations?|met(?:s|tre)\s+(?:[çc]a|le|la)\s+(?:dans\s+)?op[eé]rations?)\b/i;

const HR_REMINDER_LANE_RE =
    /\b(?:as\s+a\s+reminder\s+in\s+(?:hr|rh)|reminder\s+in\s+(?:hr|rh)|rappel\s+(?:dans\s+)?(?:hr|rh|ressources?\s+humaines?)|garder\s+(?:[çc]a\s+)?(?:comme\s+)?(?:un\s+)?rappel|keep\s+it\s+as\s+a\s+reminder|ok\s+keep\s+it\s+as\s+a\s+reminder)\b/i;

const PAYSLIP_TASK_RE =
    /\b(prepare\s+payslips?|preparing\s+payslips?|payslip\s+prep|pr[eé]parer\s+(?:les\s+)?fiches?\s+de\s+paie|fiches?\s+de\s+paie\s+(?:du\s+)?personnel|staff\s+payslips?)\b/i;

const DAILY_FREQ_RE =
    /\b(tous\s+les\s+jours|every\s+day|daily|quotidien(?:ne)?|each\s+day|chaque\s+jour)\b/i;

const FOLLOW_UP_RE =
    /\b(?:revenir\s+(?:vers|chez)|follow\s+up\s+with|get\s+back\s+to|rappeler|contact(?:er)?)\s+(.+)/i;

const PURCHASE_RE =
    /^(?:commande\s*:|order\s*:)\s*(.+)$/i;

const BUY_VERB_RE =
    /\b(?:order|purchase|buy|acheter|commander|we\s+need\s+to\s+(?:buy|order|purchase))\b/i;

const MAINTENANCE_RE =
    /\b(?:not\s+working|needs?\s+(?:to\s+be\s+)?repair(?:ed|ing)?|broken|en\s+panne|out\s+of\s+order|stopped\s+working|oven|fridge|freezer|dishwasher|ac\b|air\s+condition|plumbing|leak(?:ing)?|four\b|climat(?:isation)?|r[eé]parer|reparer|[àa]\s+r[eé]parer|wc\b|wcs\b|toilettes?|sanitaires?|restroom|bathroom|lavabos?|urinoirs?|men'?s?\s+(?:room|toilet|wc)|wc\s+hommes?|toilettes?\s+hommes?)\b/i;

const DANGER_RE =
    /\b(?:fire|smoke|gas\s+smell|injur|bleed|unconscious|food\s+poison|robbery|harass|explosion|electrocut)\b/i;

const MONTH_NAME =
    "janvier|january|jan|f[eé]vrier|february|feb|mars|march|mar|avril|april|apr|mai|may|juin|june|jun|juillet|july|jul|ao[uû]t|august|aug|septembre|september|sep|octobre|october|oct|novembre|november|nov|d[eé]cembre|december|dec";

const INVOICE_LINE_RE =
    /\bfacture\s+(\d+)\s*,?\s*(\d[\d\s.,]*)\s*(mad|eur|usd|dh|dhs)\b/i;

const INVOICE_EN_RE =
    /\binvoice\s+#?\s*(\d+)[^\d]{0,40}(?:amount\s+)?(\d[\d\s.,]*)\s*(mad|eur|usd|dh|dhs)?\b/i;

const AMOUNT_ONLY_RE =
    /\b(\d[\d\s.,]*)\s*(mad|eur|usd|dh|dhs)\b/i;

const FACTURE_NUM_RE =
    /\bfacture\s+#?\s*(\d+)\b/i;

/** Échéance — no leading \\b (JS word boundaries break on accented É). */
const ECHEANCE_DATE_RE = new RegExp(
    `(?:[eéÉ]ch[eéÉ]ance|echeance)\\s+(?:le\\s+|the\\s+)?(\\d{1,2})(?:\\s+(${MONTH_NAME}))?`,
    "i",
);

const DUE_DATE_RE = new RegExp(
    `\\b(?:due|pay(?:able)?|before|avant|pour\\s+le|deadline)\\s+(?:le\\s+|the\\s+)?(\\d{1,2})(?:\\s+(${MONTH_NAME}))?`,
    "i",
);

const AVANT_LE_DATE_RE = new RegExp(
    `\\b(?:doit\\s+(?:être\\s+)?(?:fait\\s+)?(?:avant\\s+)?|(?:avant|before)\\s+)le\\s+(\\d{1,2})(?:\\s+(${MONTH_NAME}))?`,
    "i",
);

const PAY_VENDOR_RE =
    /\b(?:pay|payer|we\s+need\s+to\s+pay)\s+(?:the\s+)?([a-zàâäéèêëïîôùûüç'-]{3,40})\b/i;

const VENDOR_HINTS: Array<{ match: RegExp; vendor: string }> = [
    { match: /\b(?:internet|wifi|wi-fi|fibre|fiber|broadband)\b/i, vendor: "Internet" },
    { match: /\b(?:baker|boulanger|boulang(?:er|ère|erie))\b/i, vendor: "Boulanger" },
    { match: /\belectric/i, vendor: "Electricity" },
    { match: /\brent\b|\bloyer\b/i, vendor: "Rent" },
];

const PAYMENT_METHOD_RE =
    /\b(virement|transfer|bank\s+transfer|card|cash|cheque|ch[eè]que|check)\b/i;

const ASSIGN_TO_COMMANDS_RE =
    /\b(?:c\s+[aà]\s+ranger\s+en\s+commandes?|(?:file|class(?:ify|er))\s+(?:under|in)\s+orders?|in\s+orders?)\b/i;

const NAME_ONLY_RE = /^[a-zàâäéèêëïîôùûüç\s'-]{3,60}$/i;

export function collectRecentText(messages: Array<{ type?: string; text?: string }>, limit = 12): string[] {
    return messages
        .filter((m) => m.type === "text" && typeof m.text === "string" && m.text.trim())
        .map((m) => m.text!.trim())
        .slice(-limit);
}

export function resolveLookupIntent(text: string): OperationCommandIntent | null {
    const t = text.trim();
    if (!t) return null;
    const idMatch = t.match(LOOKUP_ID_RE);
    if (idMatch?.[1]) {
        return { kind: "lookup", query: idMatch[1].replace(/[^a-f0-9]/gi, "") };
    }
    if (LOOKUP_PHRASE_RE.test(t)) {
        const embedded = t.match(/\b([a-f0-9]{6,12})\b/i);
        return { kind: "lookup", query: embedded?.[1] || t.slice(0, 120) };
    }
    return null;
}

export function resolveChaseIntent(text: string): OperationCommandIntent | null {
    const t = text.trim();
    if (!t || !CHASE_RE.test(t)) return null;
    if (PERSONAL_OPS_RE.test(t) || HR_REMINDER_LANE_RE.test(t) || PAYSLIP_TASK_RE.test(t)) {
        return null;
    }

    const idMatch = t.match(LOOKUP_ID_RE);
    if (idMatch?.[1]) {
        return { kind: "chase", query: idMatch[1].replace(/[^a-f0-9]/gi, "") };
    }

    const embedded = t.match(/\b([a-f0-9]{6,12})\b/i);
    if (embedded?.[1]) {
        return { kind: "chase", query: embedded[1] };
    }

    const tailMatch = t.match(
        /\b(?:follow\s*up\s+(?:with|on|about|for)|relance(?:r)?(?:\s+(?:sur|with|on|de|la|le))?|chase|rappeler?)\s+(.+)/i,
    );
    if (tailMatch?.[1]) {
        const q = tailMatch[1].replace(/[.!?]+$/, "").trim();
        if (q.length >= 3) return { kind: "chase", query: q.slice(0, 120) };
    }

    return { kind: "chase", query: t.slice(0, 120) };
}

export function resolvePayslipGenerateIntent(recentTexts: string[]): OperationCommandIntent | null {
    const joined = recentTexts.join("\n");
    if (!PAYSLIP_GENERATE_RE.test(joined)) return null;

    let staffName: string | undefined;
    const forMatch = joined.match(/\b(?:for|pour|de)\s+([a-zàâäéèêëïîôùûüç\s'-]{3,50})/i);
    if (forMatch?.[1]) staffName = forMatch[1].trim();

    let month: number | undefined;
    let year: number | undefined;
    const monthMatch = joined.match(new RegExp(`\\b(${MONTH_NAME})\\b(?:\\s+(20\\d{2}))?`, "i"));
    if (monthMatch) {
        const token = monthMatch[1].toLowerCase();
        const monthMap: Record<string, number> = {
            janvier: 1, january: 1, jan: 1, fevrier: 2, février: 2, february: 2, feb: 2,
            mars: 3, march: 3, mar: 3, avril: 4, april: 4, apr: 4, mai: 5, may: 5,
            juin: 6, june: 6, jun: 6, juillet: 7, july: 7, jul: 7, aout: 8, août: 8, august: 8, aug: 8,
            septembre: 9, september: 9, sep: 9, octobre: 10, october: 10, oct: 10,
            novembre: 11, november: 11, nov: 11, decembre: 12, décembre: 12, december: 12, dec: 12,
        };
        month = monthMap[token.replace(/[^a-zàâäéèêëïîôùûüç]/g, "")] || monthMap[token];
        if (monthMatch[2]) year = Number(monthMatch[2]);
    }
    if (!year) {
        const y = joined.match(/\b(20\d{2})\b/);
        if (y?.[1]) year = Number(y[1]);
    }
    if (!year) year = new Date().getFullYear();

    return { kind: "generate_payslip", staffName, month, year };
}

export function resolveTemperatureLogIntent(text: string): OperationCommandIntent | null {
    const t = text.trim();
    if (!t || !TEMPERATURE_LOG_RE.test(t)) return null;
    const m = t.match(/(.+?)\s+(-?\d+(?:[.,]\d+)?)\s*(?:°?\s*[cC]|degrees?\s*c|degr[eé]s?\s*c)/i);
    if (m) {
        return {
            kind: "temperature_log",
            text: t,
            equipment: m[1].trim().slice(0, 120),
            valueC: Number(m[2].replace(",", ".")),
        };
    }
    return { kind: "temperature_log", text: t };
}

export function resolveBankPaymentStatusIntent(recentTexts: string[]): OperationCommandIntent | null {
    const joined = recentTexts.join("\n");
    if (!BANK_PAYMENT_STATUS_RE.test(joined)) return null;

    let status = "INITIATED";
    if (/\b(?:cleared|paid|received|encaiss[eé]|re[çc]u)\b/i.test(joined)) status = "CLEARED";
    if (/\b(?:failed|bounced|rejected|[eé]chou[eé]|rejet[eé])\b/i.test(joined)) status = "FAILED";

    const vendorMatch = joined.match(/\b(?:for|pour|vendor|fournisseur)\s+([a-zàâäéèêëïîôùûüç0-9\s'-]{3,40})/i);
    const invMatch = joined.match(/\bfacture\s+#?\s*(\d+)\b/i);
    const refMatch = joined.match(/\b(?:ref(?:erence)?|r[eé]f)\s+#?\s*(\w+)/i);

    return {
        kind: "bank_payment_status",
        vendor: vendorMatch?.[1]?.trim(),
        invoiceNumber: invMatch?.[1],
        status,
        reference: refMatch?.[1],
        note: joined.slice(0, 200),
    };
}

export function resolveDeliveryMenuSyncIntent(text: string): OperationCommandIntent | null {
    if (!DELIVERY_MENU_SYNC_RE.test(text)) return null;
    const provider = /\bglovo\b/i.test(text) ? "GLOVO" : "GLOVO";
    return { kind: "delivery_menu_sync", provider };
}

export function resolveComplianceSeedIntent(text: string): OperationCommandIntent | null {
    if (!COMPLIANCE_SEED_RE.test(text)) return null;
    if (/\b(?:generate|payslip|fiche\s+de\s+paie)\b/i.test(text) && !/\bcnss\b/i.test(text)) return null;
    return { kind: "seed_compliance" };
}

function parseFollowUpTitle(text: string): string | null {
    const m = text.match(FOLLOW_UP_RE);
    if (!m?.[1]) return null;
    let title = m[1]
        .replace(/\b(?:pour|about|regarding|concernant)\b/gi, "—")
        .replace(/\s+/g, " ")
        .trim();
    title = title.replace(/[.!?]+$/, "").trim();
    return title.length >= 4 ? title.slice(0, 255) : null;
}

export function resolvePersonalOpsReminder(recentTexts: string[]): OperationCommandIntent | null {
    const joined = recentTexts.join("\n");
    const last = recentTexts[recentTexts.length - 1] || "";
    const wantsOps =
        PERSONAL_OPS_RE.test(last) ||
        PERSONAL_OPS_RE.test(joined) ||
        /\bop[eé]rations?\b/i.test(last);

    let title = parseFollowUpTitle(joined) || parseFollowUpTitle(last);
    if (!title) {
        for (const line of [...recentTexts].reverse()) {
            title = parseFollowUpTitle(line);
            if (title) break;
        }
    }

    if (!title) return null;
    if (!wantsOps && !PERSONAL_OPS_RE.test(joined)) {
        const priorOps = recentTexts.some((l) => PERSONAL_OPS_RE.test(l) || /\bdans\s+op[eé]rations?\b/i.test(l));
        if (!priorOps) return null;
    }

    return {
        kind: "personal_ops_reminder",
        title: title.charAt(0).toUpperCase() + title.slice(1),
        description: joined.slice(0, 500),
    };
}

export function resolveDashboardReminderIntent(recentTexts: string[]): OperationCommandIntent | null {
    const joined = recentTexts.join("\n").toLowerCase();
    const payslipTopic = PAYSLIP_TASK_RE.test(joined);
    const wantsHrLane =
        HR_REMINDER_LANE_RE.test(joined) ||
        /\b(?:in\s+hr|dans\s+(?:hr|rh)|hr\s+reminder|rappel\s+rh)\b/i.test(joined);
    const daily = DAILY_FREQ_RE.test(joined);

    if (!payslipTopic && !wantsHrLane) return null;

    // Need either explicit HR lane OR payslip topic + confirmation to save as reminder.
    const confirmed =
        wantsHrLane ||
        HR_REMINDER_LANE_RE.test(recentTexts[recentTexts.length - 1] || "") ||
        daily;

    if (!payslipTopic && !confirmed) return null;
    if (payslipTopic && !confirmed && recentTexts.length < 2) return null;

    let title = "Prepare staff payslips";
    for (const line of recentTexts) {
        if (PAYSLIP_TASK_RE.test(line)) {
            title = line.trim().slice(0, 255);
            break;
        }
    }

    const freqNote = daily ? "Daily reminder." : "Recurring reminder.";
    return {
        kind: "dashboard_reminder",
        title: title.charAt(0).toUpperCase() + title.slice(1),
        description: `${freqNote} ${recentTexts.join(" | ").slice(0, 400)}`,
        category: "PAYROLL",
        widgetId: "human_resources",
    };
}

function extractPurchaseBody(text: string): string | null {
    const cmd = text.match(PURCHASE_RE);
    if (cmd?.[1]) return cmd[1].trim();
    if (BUY_VERB_RE.test(text) && /\d+\s+\w+|\bbouteilles?\b|\bbottles?\b|\bcases?\b/i.test(text)) {
        return text.trim();
    }
    return null;
}

export function resolvePurchaseOrderIntent(recentTexts: string[]): OperationCommandIntent | null {
    const last = recentTexts[recentTexts.length - 1] || "";
    let body: string | null = null;
    let assigneeName: string | undefined;

    for (let i = recentTexts.length - 1; i >= 0; i--) {
        const line = recentTexts[i];
        if (!body) body = extractPurchaseBody(line);
        if (!assigneeName && NAME_ONLY_RE.test(line.trim()) && line.trim().split(/\s+/).length >= 2) {
            assigneeName = line.trim();
        }
    }

    if (!body && !extractPurchaseBody(last)) return null;
    body = body || extractPurchaseBody(last);
    if (!body) return null;

    const urgent = /\b(?:urgent|asap|avant\s+(?:jeudi|demain|lundi)|before\s+(?:thursday|tomorrow|monday))\b/i.test(body);
    const subject = `Purchase: ${body.slice(0, 72)}`;

    return {
        kind: "purchase_order",
        subject,
        description: body,
        assigneeName,
        priority: urgent ? "HIGH" : "MEDIUM",
    };
}

export function resolveMaintenanceIntent(text: string): OperationCommandIntent | null {
    const t = text.trim();
    if (!t || t.length < 8) return null;
    if (!MAINTENANCE_RE.test(t) || DANGER_RE.test(t)) return null;

    const urgent =
        /\b(?:urgent|asap|before\s+(?:dinner|service|saturday|tomorrow)|super\s+soon|avant\s+(?:le\s+d[iî]ner|samedi|demain))\b/i.test(
            t,
        );

    let subject = "Equipment repair needed";
    const wcMatch = t.match(/\b(?:wc|toilettes?)\s+(?:des?\s+)?hommes?\b/i);
    const wcEnMatch = t.match(/\bmen'?s?\s+(?:room|toilet|wc|restroom)s?\b/i);
    if (wcMatch || wcEnMatch) {
        subject = "Men's restroom repair";
    } else {
        const subjectMatch = t.match(
            /\b(?:the\s+)?(\w+(?:\s+\w+)?)\s+(?:is|are|really\s+not)\s+(?:not\s+working|broken|down)/i,
        );
        if (subjectMatch?.[1]) {
            subject = `${subjectMatch[1].charAt(0).toUpperCase()}${subjectMatch[1].slice(1)} needs repair`;
        } else if (/\br[eé]parer\b/i.test(t)) {
            subject = t.slice(0, 72).replace(/^[iI]l faut\s+/i, "").trim() || subject;
            subject = subject.charAt(0).toUpperCase() + subject.slice(1);
        }
    }

    return {
        kind: "maintenance",
        subject: subject.slice(0, 80),
        description: t,
        priority: urgent ? "HIGH" : "MEDIUM",
    };
}

function parseAmount(raw: string): number | null {
    const n = Number(String(raw).replace(/\s/g, "").replace(",", "."));
    return Number.isFinite(n) && n > 0 ? n : null;
}

function parseDueDateFromText(text: string, referenceYear = new Date().getFullYear()): string | null {
    const normalized = text.normalize("NFC");
    const m =
        normalized.match(ECHEANCE_DATE_RE) ||
        normalized.match(AVANT_LE_DATE_RE) ||
        normalized.match(DUE_DATE_RE);
    if (!m?.[1]) return null;
    const day = Number(m[1]);
    if (!Number.isFinite(day) || day < 1 || day > 31) return null;

    const monthMap: Record<string, number> = {
        jan: 1,
        january: 1,
        janvier: 1,
        feb: 2,
        february: 2,
        fevrier: 2,
        février: 2,
        mar: 3,
        march: 3,
        mars: 3,
        apr: 4,
        april: 4,
        avril: 4,
        may: 5,
        mai: 5,
        jun: 6,
        june: 6,
        juin: 6,
        jul: 7,
        july: 7,
        juillet: 7,
        aug: 8,
        august: 8,
        aout: 8,
        août: 8,
        sep: 9,
        september: 9,
        septembre: 9,
        oct: 10,
        october: 10,
        octobre: 10,
        nov: 11,
        november: 11,
        novembre: 11,
        dec: 12,
        december: 12,
        decembre: 12,
        décembre: 12,
    };

    let month = new Date().getMonth() + 1;
    const monthToken = (m[2] || "").toLowerCase().replace(/[^a-zàâäéèêëïîôùûüç]/g, "");
    if (monthToken && monthMap[monthToken]) {
        month = monthMap[monthToken];
    }

    const pad = (n: number) => String(n).padStart(2, "0");
    return `${referenceYear}-${pad(month)}-${pad(day)}`;
}

function inferVendor(joined: string): string {
    for (const { match, vendor } of VENDOR_HINTS) {
        if (match.test(joined)) return vendor;
    }
    const payMatch = joined.match(PAY_VENDOR_RE);
    if (payMatch?.[1]) {
        const raw = payMatch[1].trim();
        if (!/^(the|le|la|les|a|an|we|need|to)$/i.test(raw)) {
            return raw.charAt(0).toUpperCase() + raw.slice(1);
        }
    }
    return "Vendor";
}

export function resolveInvoiceIntent(recentTexts: string[]): OperationCommandIntent | null {
    const joined = recentTexts.join("\n");
    let invoiceNumber: string | undefined;
    let amount: number | null = null;
    let currency: string | undefined;
    let dueDate: string | null = null;
    let paymentMethod: string | undefined;

    for (const line of recentTexts) {
        const fr = line.match(INVOICE_LINE_RE);
        const en = line.match(INVOICE_EN_RE);
        const hit = fr || en;
        if (hit) {
            invoiceNumber = hit[1];
            const parsed = parseAmount(hit[2]);
            if (parsed != null) {
                amount = parsed;
                currency = (hit[3] || "").toUpperCase() || undefined;
            }
        }

        if (!invoiceNumber) {
            const fn = line.match(FACTURE_NUM_RE);
            if (fn?.[1]) invoiceNumber = fn[1];
        }
        if (amount == null) {
            const am = line.match(AMOUNT_ONLY_RE);
            if (am) {
                amount = parseAmount(am[1]);
                currency = (am[2] || "").toUpperCase() || undefined;
            }
        }

        const due = parseDueDateFromText(line);
        if (due) dueDate = due;

        const pm = line.match(PAYMENT_METHOD_RE);
        if (pm?.[1]) paymentMethod = pm[1];
    }

    // Pay-vendor topic alone isn't enough — need amount + due date from follow-up.
    const payTopic = /\b(?:pay|payer|invoice|facture|bill)\b/i.test(joined);
    if (!amount || !dueDate) return null;
    if (!payTopic && !invoiceNumber) return null;

    const vendor = inferVendor(joined);
    const notes = paymentMethod ? `Payment method: ${paymentMethod}` : undefined;

    return {
        kind: "record_invoice",
        vendor,
        amount,
        dueDate,
        invoiceNumber,
        notes,
        currency: currency || "MAD",
        paymentMethod,
    };
}

export function resolveOperationsCommandIntent(
    messages: Array<{ type?: string; text?: string }>,
): OperationCommandIntent | null {
    const recent = collectRecentText(messages, 12);
    if (recent.length === 0) return null;

    const last = recent[recent.length - 1];

    const lookup = resolveLookupIntent(last);
    if (lookup) return lookup;

    const chase = resolveChaseIntent(last);
    if (chase) return chase;

    const payslip = resolvePayslipGenerateIntent(recent);
    if (payslip) return payslip;

    const temperature = resolveTemperatureLogIntent(last);
    if (temperature) return temperature;

    const bankPay = resolveBankPaymentStatusIntent(recent);
    if (bankPay) return bankPay;

    const deliveryMenu = resolveDeliveryMenuSyncIntent(last);
    if (deliveryMenu) return deliveryMenu;

    const compliance = resolveComplianceSeedIntent(last);
    if (compliance) return compliance;

    const invoice = resolveInvoiceIntent(recent);
    if (invoice) return invoice;

    const hrReminder = resolveDashboardReminderIntent(recent);
    if (hrReminder) return hrReminder;

    const maintenance = resolveMaintenanceIntent(last);
    if (maintenance) return maintenance;

    // Also scan earlier lines — user may have sent repair request then a follow-up.
    if (!maintenance) {
        for (const line of [...recent].reverse()) {
            const m = resolveMaintenanceIntent(line);
            if (m) return m;
        }
    }

    const purchase = resolvePurchaseOrderIntent(recent);
    if (purchase) {
        const hasPurchaseSignal = recent.some(
            (l) => PURCHASE_RE.test(l) || BUY_VERB_RE.test(l) || ASSIGN_TO_COMMANDS_RE.test(l),
        );
        if (hasPurchaseSignal) return purchase;
    }

    const opsReminder = resolvePersonalOpsReminder(recent);
    if (opsReminder) return opsReminder;

    return null;
}
