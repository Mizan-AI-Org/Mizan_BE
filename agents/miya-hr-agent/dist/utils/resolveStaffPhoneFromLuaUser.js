/**
 * Resolve the staff WhatsApp / phone digits for tools that call Mizan "by phone" APIs.
 * Matches Django `normalize_activation_phone_inbound` for Morocco (national → 212…).
 */
export function toMoroccoE164Digits(digits) {
    const d = String(digits ?? "")
        .replace(/\D/g, "")
        .trim();
    if (!d || d.length < 6) {
        return d;
    }
    if (d.length === 9 && /^[67]/.test(d)) {
        return `212${d}`;
    }
    if (d.length === 10 && d.startsWith("0") && /^[67]/.test(d[1] || "")) {
        return `212${d.slice(1)}`;
    }
    return d;
}
function digitsOnly(p) {
    return String(p ?? "").replace(/\D/g, "");
}
/**
 * Prefer explicit tool input, then synced user.data / Lua profile / metadata, then any uid
 * segment that yields at least 6 digits (fixes `whatsapp:+212…` where split(':')[1] is "").
 */
export function resolveStaffPhoneForByPhoneTools(user, inputPhone) {
    const tryNorm = (raw) => {
        const d = digitsOnly(raw);
        if (d.length < 6) {
            return "";
        }
        return toMoroccoE164Digits(d);
    };
    const ordered = [];
    if (inputPhone) {
        ordered.push(inputPhone);
    }
    if (user) {
        const data = user.data || {};
        const profile = user._luaProfile || {};
        const meta = profile.metadata &&
            typeof profile.metadata === "object"
            ? profile.metadata
            : {};
        ordered.push(data.phone, profile.phoneNumber, profile.mobileNumber, data.whatsappPhone, profile.whatsappPhone, meta.phone);
        const uid = user.uid != null ? String(user.uid).trim() : "";
        if (uid) {
            if (!uid.includes(":")) {
                ordered.push(uid);
            }
            else {
                for (const part of uid.split(":").map((p) => p.trim()).filter(Boolean)) {
                    if (digitsOnly(part).length >= 6) {
                        ordered.push(part);
                    }
                }
            }
        }
    }
    for (const raw of ordered) {
        const n = tryNorm(raw);
        if (n) {
            return n;
        }
    }
    return "";
}
