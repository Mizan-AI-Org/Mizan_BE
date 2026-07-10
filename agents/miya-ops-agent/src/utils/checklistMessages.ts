/**
 * Natural, varied checklist copy for WhatsApp — avoids robotic "✓ Recorded." every step.
 */

export type ChecklistTaskLike = {
    index: number;
    title: string;
    description?: string;
};

const ACKS = [
    "Got it.",
    "Noted.",
    "Perfect.",
    "Thanks.",
    "Alright.",
    "Okay, marked.",
];

const NEXT_INTROS = [
    "Next up",
    "Here's the next one",
    "Moving on",
    "Next task",
];

function pick<T>(arr: T[], seed: number): T {
    return arr[Math.abs(seed) % arr.length];
}

export function formatChecklistTaskPrompt(
    task: ChecklistTaskLike,
    total: number,
    opts?: { isFirst?: boolean; answered?: number },
): string {
    const desc = (task.description || "").trim();
    const head = opts?.isFirst
        ? `Your shift has ${total} task${total === 1 ? "" : "s"}. Here's your first one:`
        : `${pick(ACKS, task.index + (opts?.answered || 0))} ${pick(NEXT_INTROS, task.index)}:`;

    const lines = [
        head,
        "",
        `*Task ${task.index}/${total}:* ${task.title}`,
    ];
    if (desc) lines.push(desc);
    lines.push("", "Reply *Yes*, *No*, or *N/A*.");
    return lines.join("\n");
}

export function formatChecklistComplete(summary: {
    yes?: number;
    no?: number;
    n_a?: number;
    total?: number;
}): string {
    const yes = summary.yes || 0;
    const no = summary.no || 0;
    const na = summary.n_a || 0;
    const total = summary.total || yes + no + na;
    const parts: string[] = [];
    if (yes) parts.push(`${yes} done`);
    if (no) parts.push(`${no} still open`);
    if (na) parts.push(`${na} skipped`);
    const detail = parts.length ? ` (${parts.join(", ")} out of ${total})` : "";
    return `Nice work — checklist complete${detail}. Have a great shift!`;
}

export function formatChecklistStartIntro(total: number, first: ChecklistTaskLike): string {
    return formatChecklistTaskPrompt(first, total, { isFirst: true });
}
