import { z } from "zod";

/** Canonical incident types used everywhere (Miya determines type; title is constant per type). */
export const INCIDENT_CATEGORIES = ["Safety", "Maintenance", "HR", "Service", "General"] as const;
export type IncidentCategory = (typeof INCIDENT_CATEGORIES)[number];

const SAFETY_KEYWORDS = [
    'injury', 'hurt', 'slip', 'fall', 'bleed', 'burn', 'fire', 'unsafe', 'hazard', 'accident',
    'cut', 'wound', 'glass', 'spill', 'wet floor', 'sharp',
    'إصابة', 'انزلاق', 'سقوط', 'حرق', 'حادث', 'خطر', 'نار',
    'blessure', 'glissade', 'chute', 'brûlure', 'incident', 'danger', 'feu', 'accident',
];

const MAINTENANCE_KEYWORDS = [
    'broken', 'leak', 'maintenance', 'machine', 'equipment', 'fridge', 'freezer', 'oven',
    'gas', 'water', 'light', 'door', 'plumbing', 'electrical', 'repair', 'not working',
    'مكسور', 'تسرب', 'صيانة', 'جهاز', 'فرن', 'غاز', 'ماء', 'ثلاجة',
    'cassé', 'fuite', 'maintenance', 'machine', 'équipement', 'frigo', 'four', 'gaz', 'eau',
];

const HR_KEYWORDS = [
    'harassment', 'complaint', 'staff issue', 'conflict', 'absent', 'late', 'misconduct',
    'theft', 'stealing', 'hostile', 'discrimination', 'fight', 'argument',
    'تحرش', 'شكوى', 'سرقة', 'غياب', 'تأخر',
    'harcèlement', 'plainte', 'vol', 'absence', 'retard', 'conflit',
];

const SERVICE_KEYWORDS = [
    'customer', 'guest', 'complaint', 'service', 'order wrong', 'cold food', 'waiting',
    'rude', 'slow', 'dirty', 'unclean', 'quality',
    'زبون', 'خدمة', 'وسخ', 'بارد',
    'client', 'service', 'sale', 'froid', 'attente',
];

const CRITICAL_KEYWORDS = ['fire', 'flood', 'gas leak', 'collapse', 'ambulance', 'emergency', 'نار', 'طوارئ', 'incendie', 'urgence'];
const HIGH_KEYWORDS = ['injury', 'hurt', 'bleed', 'broken', 'theft', 'إصابة', 'سرقة', 'blessure', 'vol'];

export function normalizeIncidentCategory(value: string | undefined): IncidentCategory {
    if (!value || typeof value !== "string") return "General";
    const v = value.trim();
    if (INCIDENT_CATEGORIES.includes(v as IncidentCategory)) return v as IncidentCategory;
    const lower = v.toLowerCase();
    if (["safety", "security"].some((x) => lower.includes(x))) return "Safety";
    if (["maintenance", "equipment", "broken", "repair"].some((x) => lower.includes(x))) return "Maintenance";
    if (["hr", "human resources", "harassment", "complaint about staff"].some((x) => lower.includes(x))) return "HR";
    if (["service", "customer", "guest", "complaint"].some((x) => lower.includes(x))) return "Service";
    return "General";
}

function inferCategory(text: string): IncidentCategory {
    const lower = text.toLowerCase();
    if (SAFETY_KEYWORDS.some(k => lower.includes(k))) return "Safety";
    if (MAINTENANCE_KEYWORDS.some(k => lower.includes(k))) return "Maintenance";
    if (HR_KEYWORDS.some(k => lower.includes(k))) return "HR";
    if (SERVICE_KEYWORDS.some(k => lower.includes(k))) return "Service";
    return "General";
}

function inferPriority(text: string): string {
    const lower = text.toLowerCase();
    if (CRITICAL_KEYWORDS.some(k => lower.includes(k))) return "CRITICAL";
    if (HIGH_KEYWORDS.some(k => lower.includes(k))) return "HIGH";
    return "MEDIUM";
}

function suggestAction(category: IncidentCategory, priority: string): string {
    if (priority === "CRITICAL") return "Respond immediately. Ensure staff and guest safety.";
    if (category === "Safety") return "Inspect the area and ensure staff safety.";
    if (category === "Maintenance") return "Schedule repair or contact maintenance team.";
    if (category === "HR") return "Review with management and follow HR procedures.";
    if (category === "Service") return "Address with staff and follow up with the guest.";
    return "Review details and take appropriate action.";
}

export class IncidentManagementModule {
    async analyzeIncident(description: string, metadata: any = {}) {
        const category = inferCategory(description);
        const priority = inferPriority(description);

        return {
            analysis: {
                issueDescription: description.substring(0, 500),
                category,
                priority,
                suggestedAction: suggestAction(category, priority),
            },
            timestamp: new Date().toISOString(),
        };
    }
}
