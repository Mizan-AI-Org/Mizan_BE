/**
 * Natural, varied checklist copy for WhatsApp — avoids robotic "✓ Recorded." every step.
 * Prefer Django `message_for_user` when present; these are localized fallbacks (en/fr/ar).
 */

export type ChecklistTaskLike = {
    index: number;
    title: string;
    description?: string;
    requires_photo?: boolean;
};

export type ChecklistLang = "en" | "fr" | "ar";

function normalizeLang(raw?: string | null): ChecklistLang {
    const v = String(raw || "en").trim().toLowerCase();
    if (v === "fr" || v.startsWith("fr")) return "fr";
    if (v === "ar" || v === "ma" || v.startsWith("ar")) return "ar";
    return "en";
}

const COPY = {
    en: {
        first: (total: number) =>
            `Your shift has ${total} task${total === 1 ? "" : "s"}. Here's your first one:`,
        next: "Got it. Next up:",
        task: (i: number, total: number, title: string) => `*Task ${i}/${total}:* ${title}`,
        reply: "Reply *Yes*, *No*, or *N/A*.",
        replyPhoto:
            "Reply *Yes*, *No*, or *N/A*. If you say Yes, I'll ask for a quick photo as proof.",
        photoHead: "Great — please send a *photo* as proof for:",
        photoTail: "Open your camera and send the picture here, then I'll continue.",
        complete: (yes: number, no: number, na: number, total: number) =>
            `Nice work — checklist complete! ${yes} done, ${no} still open, ${na} skipped out of ${total} tasks. Have a great shift!`,
        clockedHint: "Say *start checklist* when you're ready and I'll walk you through them.",
        clockInHint:
            "Clock in first, then say *start checklist* and I'll walk you through them.",
    },
    fr: {
        first: (total: number) =>
            `Votre service a ${total} tâche${total === 1 ? "" : "s"}. Voici la première :`,
        next: "Noté. Ensuite :",
        task: (i: number, total: number, title: string) => `*Tâche ${i}/${total} :* ${title}`,
        reply: "Répondez *Oui*, *Non* ou *N/A*.",
        replyPhoto:
            "Répondez *Oui*, *Non* ou *N/A*. Si vous dites Oui, je demanderai une photo comme preuve.",
        photoHead: "Parfait — envoyez une *photo* comme preuve pour :",
        photoTail: "Ouvrez l’appareil photo et envoyez l’image ici, puis je continue.",
        complete: (yes: number, no: number, na: number, total: number) =>
            `Bravo — checklist terminée ! ${yes} faites, ${no} encore ouvertes, ${na} ignorées sur ${total} tâches. Bon service !`,
        clockedHint:
            "Dites *démarrer la checklist* quand vous êtes prêt et je vous guide étape par étape.",
        clockInHint:
            "Pointez d’abord, puis dites *démarrer la checklist* et je vous guide.",
    },
    ar: {
        first: (total: number) => `ورديتك فيها ${total} مهمة/مهام. إليك الأولى:`,
        next: "تم. التالية:",
        task: (i: number, total: number, title: string) => `*المهمة ${i}/${total}:* ${title}`,
        reply: "أجب بـ *نعم* أو *لا* أو *غير منطبق*.",
        replyPhoto: "أجب بـ *نعم* أو *لا* أو *غير منطبق*. إذا قلت نعم، سأطلب صورة كإثبات.",
        photoHead: "ممتاز — أرسل *صورة* كإثبات لـ:",
        photoTail: "افتح الكاميرا وأرسل الصورة هنا، ثم أكمل.",
        complete: (yes: number, no: number, na: number, total: number) =>
            `أحسنت — اكتملت قائمة التحقق! ${yes} منجزة، ${no} ما زالت مفتوحة، ${na} متخطّاة من أصل ${total}. وردية موفّقة!`,
        clockedHint: "قل *ابدأ المهام* عندما تكون جاهزاً وسأرشدك خطوة بخطوة.",
        clockInHint: "سجّل الحضور أولاً، ثم قل *ابدأ المهام* وسأرشدك.",
    },
} as const;

export function formatChecklistTaskPrompt(
    task: ChecklistTaskLike,
    total: number,
    opts?: { isFirst?: boolean; answered?: number; lang?: string | null },
): string {
    const c = COPY[normalizeLang(opts?.lang)];
    const desc = (task.description || "").trim();
    const head = opts?.isFirst ? c.first(total) : c.next;
    const lines = [head, "", c.task(task.index, total, task.title)];
    if (desc) lines.push(desc);
    lines.push("", task.requires_photo ? c.replyPhoto : c.reply);
    return lines.join("\n");
}

export function formatPhotoAwaitPrompt(
    task: { title?: string; description?: string },
    lang?: string | null,
): string {
    const c = COPY[normalizeLang(lang)];
    const title = (task.title || "—").trim();
    const desc = (task.description || "").trim();
    const lines = [c.photoHead, "", `*${title}*`];
    if (desc) lines.push(desc);
    lines.push("", c.photoTail);
    return lines.join("\n");
}

export function formatChecklistComplete(
    summary: { yes?: number; no?: number; n_a?: number; total?: number },
    lang?: string | null,
): string {
    const c = COPY[normalizeLang(lang)];
    const yes = summary.yes || 0;
    const no = summary.no || 0;
    const na = summary.n_a || 0;
    const total = summary.total || yes + no + na;
    return c.complete(yes, no, na, total);
}

export function formatChecklistStartIntro(
    total: number,
    first: ChecklistTaskLike,
    lang?: string | null,
): string {
    return formatChecklistTaskPrompt(first, total, { isFirst: true, lang });
}

export function formatChecklistClockHint(clockedIn: boolean, lang?: string | null): string {
    const c = COPY[normalizeLang(lang)];
    return clockedIn ? c.clockedHint : c.clockInHint;
}
