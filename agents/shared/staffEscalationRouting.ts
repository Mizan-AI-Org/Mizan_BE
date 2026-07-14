/**
 * Detect staff → manager escalations (wages, payslip, HR docs, absence).
 * Keep in sync with staff/whatsapp_escalation.py (Django source of truth).
 */

export type StaffRouteKind =
    | "PAYROLL"
    | "DOCUMENT"
    | "HR"
    | "SCHEDULING"
    | "MAINTENANCE"
    | "OTHER";

export const TELL_MANAGER_RE =
    /\b(tell\s+(me\s+)?(the\s+)?(my\s+)?manager|pass\s+(this\s+)?(to|on\s+to)\s+(my\s+)?manager|let\s+(the\s+)?(my\s+)?manager\s+know|inform\s+(the\s+)?(my\s+)?manager|message\s+(the\s+)?(my\s+)?manager|ask\s+(the\s+)?(my\s+)?manager|dis\s+[àa]\s+(mon\s+)?(manager|responsable|patron)|قل\s+(ل|لـ)?(المدير|المانجر|المسؤول))\b/i;

/** Space may rephrase staff asks as "inform the manager that …" when delegating to comms. */
export const SPACE_INFORM_MANAGER_RE =
    /\b(inform|tell|let|notify)\s+(the\s+)?manager\b/i;

export const PAYROLL_RE =
    /\b(pay\s*slip|payslip|pay\s*stub|salary\s+slip|bulletin\s+de\s+paie|fiche\s+de\s+paie|كشف\s+الراتب|ورقة\s+الأجر|my\s+pay|last\s+\d+\s+months?\s+pay|wages?|salary|unpaid\s+(pay|wages?|salary)|missing\s+(pay|wages?|salary)|haven['']?t\s+received\s+(my\s+)?(pay|wages?|salary|last)|yet\s+to\s+receive\s+(my\s+)?(pay|wages?|salary|last)|didn['']?t\s+(get|receive)\s+(my\s+)?(pay|wages?|salary)|last\s+week['']?s?\s+wages?|last\s+\d+\s+weeks?\s*wages?|weekswages|early\s+(pay|page|payment|salary)|salary\s+advance|advance\s+(pay|payment|salary)|pay\s+(me\s+)?early|avance\s+sur\s+salaire|paie\s+anticipée|paie|salaire|أجرى|راتبي)\b/i;

const DOCUMENT_RE =
    /\b(visa|passport|work\s+permit|certificate|attestation|document|papers|وثيقة|تأشيرة|شهادة)\b/i;

const HR_RE =
    /\b(leave\s+request|time\s+off|vacation|holiday|sick\s+day|hr\s+request|cong[eé]|arrêt\s+maladie|إجازة)\b/i;

/** Call-in sick / can't come / headache — escalate as HR StaffRequest, never leave-flow invents. */
export const ABSENCE_RE =
    /\b(can\s*['']?t|cannot|can\s+not|won['']?t)\s+(come|make\s+it|be\s+at\s+work|work)|\b(not\s+coming|not\s+able\s+to\s+(come|work)|off\s+(work|today|tomorrow)|call\s+in\s+sick|sick\s+leave|absen(t|ce)|headache|not\s+feeling\s+well|feeling\s+(sick|unwell|ill)|mal\s+de\s+tête|malade|je\s+ne\s+peux\s+pas\s+venir|صداع|مريض|ما\s+نقدرش\s+نجي)\b/i;

const SCHEDULING_RE =
    /\b(swap\s+(my\s+)?shift|change\s+(my\s+)?shift|cover\s+(my\s+)?shift|schedule\s+change|تبديل\s+الشيفت)\b/i;

const MAINTENANCE_RE =
    /\b(leak|not\s+working|repair|fix\s+the|maintenance|en\s+panne|fuite|خاسر|معطل|(?:broken|down)\s+(?:fridge|freezer|oven|dishwasher|ac|equipment|machine))\b/i;

const MANAGER_TARGET_RE = /^(manager|my\s+manager|the\s+manager|responsable|patron|مدير|المدير)$/i;

export function classifyStaffEscalation(
    text: string,
): { category: StaffRouteKind; subject: string } | null {
    const t = text.trim();
    if (!t || t.length < 8) return null;

    const isPayroll = PAYROLL_RE.test(t);
    const isDoc = DOCUMENT_RE.test(t);
    const isHr = HR_RE.test(t) || ABSENCE_RE.test(t);
    const isSched = SCHEDULING_RE.test(t);
    const isMaint = MAINTENANCE_RE.test(t);

    const wantsManager =
        TELL_MANAGER_RE.test(t) ||
        (SPACE_INFORM_MANAGER_RE.test(t) &&
            (isPayroll || isDoc || isHr || isSched || isMaint));

    if (wantsManager) {
        if (isPayroll) return { category: "PAYROLL", subject: t.slice(0, 200) };
        if (isDoc) return { category: "DOCUMENT", subject: t.slice(0, 200) };
        if (isHr) return { category: "HR", subject: t.slice(0, 200) };
        if (isSched) return { category: "SCHEDULING", subject: t.slice(0, 200) };
        if (isMaint) return { category: "MAINTENANCE", subject: t.slice(0, 200) };
        return { category: "OTHER", subject: t.slice(0, 200) };
    }

    if (
        isPayroll &&
        /\b(need|want|ask|request|can\s+i|please|haven['']?t|yet\s+to|didn['']?t|missing|unpaid|بغيت|خاصني|je\s+veux|j['']ai\s+besoin)\b/i.test(
            t,
        )
    ) {
        return { category: "PAYROLL", subject: t.slice(0, 200) };
    }
    if (isDoc && /\b(need|want|apply|request|please|بغيت|خاصني|je\s+veux|demande)\b/i.test(t)) {
        return { category: "DOCUMENT", subject: t.slice(0, 200) };
    }
    if (
        isHr &&
        /\b(need|want|ask|request|can\s*['']?t|cannot|can\s+not|won['']?t|please|بغيت|خاصني|je\s+veux|demande)\b/i.test(
            t,
        )
    ) {
        return { category: "HR", subject: t.slice(0, 200) };
    }

    return null;
}

/** True when inform_staff is being misused for a staff payroll/HR escalation to manager. */
export function isMisroutedStaffEscalation(
    message: string,
    staffNames?: string[],
): boolean {
    if (classifyStaffEscalation(message)) return true;
    const names = (staffNames || []).map((n) => n.trim()).filter(Boolean);
    if (names.length === 0) return false;
    const targetsManager = names.some((n) => MANAGER_TARGET_RE.test(n));
    return targetsManager && PAYROLL_RE.test(message);
}
