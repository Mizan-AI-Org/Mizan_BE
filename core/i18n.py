"""
Lightweight backend i18n helpers for operational messaging.

Goals:
- Use staff preferred language when available; otherwise fall back to restaurant.language; else English.
- Never mix languages within one message (format all dynamic tokens consistently).
- Graceful fallback to English if a translation key is missing.
- Log missing translation keys for completion later.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = ("en", "fr", "ar")


def normalize_language(value: str | None) -> str:
    if not value:
        return "en"
    v = str(value).strip().lower()
    # Back-compat: older builds stored "ma" (Darija); treat as Arabic system language.
    if v in ("ma", "ar-ma"):
        return "ar"
    if v in SUPPORTED_LANGUAGES:
        return v
    return "en"


def get_effective_language(*, user=None, restaurant=None, fallback: str = "en") -> str:
    """
    Resolve effective language for a message recipient.
    Priority:
    1) user.preferred_language (if set)
    2) restaurant.language (if set)
    3) fallback (default: en)
    """
    try:
        pref = getattr(user, "preferred_language", None)
        if pref:
            return normalize_language(pref)
    except Exception:
        pass

    try:
        rest = restaurant or getattr(user, "restaurant", None)
        if rest is not None:
            return normalize_language(getattr(rest, "language", None))
    except Exception:
        pass

    return normalize_language(fallback)


def whatsapp_language_code(lang: str) -> str:
    """
    WhatsApp Cloud API language codes.
    Keep conservative defaults to avoid template mismatch.
    """
    lang = normalize_language(lang)
    if lang == "fr":
        return "fr_FR"
    if lang == "ar":
        return "ar"
    return "en_US"


# -------------------------------------------------------------------------
# Simple message catalog for operational notifications/templates
# -------------------------------------------------------------------------

_CATALOG: dict[str, dict[str, str]] = {
    "en": {
        "time.minutes_from_now": "{n} minutes",
        "time.in_minutes": "in {n} minutes",
        "shift.role.fallback": "STAFF",
        "notify.shift.assigned.title": "New Shift Assigned",
        "notify.shift.assigned.body": "You have been assigned a new shift on {start}.",
        "notify.shift.updated.title": "Shift Updated",
        "notify.shift.updated.body": "Your shift on {start} has been updated.",
        "notify.shift.cancelled.title": "Shift Cancelled",
        "notify.shift.cancelled.body": "Your shift on {start} has been cancelled.",
        "notify.shift.reminder.title": "Upcoming Shift Reminder",
        "notify.shift.reminder.body": "Reminder: You have a shift starting soon on {start}.",
        "checklist.reminder": "Hi {name}! You have {count} tasks assigned for your shift.\n\nPreview: {preview}\n\nGood luck!",
        "checklist.preview.more": "...",
        # Process start (manager Play → staff WA)
        "process.started.wa": (
            "📋 New process for you: *{name}*\n\n"
            "When you're clocked in, say *start checklist* (or *démarrer la checklist* / *ابدأ المهام*) "
            "to Miya and I'll walk you through it."
        ),
        # Checklist conversational
        "checklist.none": "No tasks or checklists are assigned to your shift right now. You're all set!",
        "checklist.already_complete": "Your checklist is already complete. Great work!",
        "checklist.already_complete_shift": "Your checklist is already complete. Have a productive shift!",
        "checklist.need_clock_in": (
            "You need to clock in before starting your checklist. "
            "Please clock in first, then ask me to start your checklist."
        ),
        "checklist.load_error": "I'm having trouble loading your checklist. Please try again.",
        "checklist.no_active": "You don't have an active checklist. Say *start checklist* to begin.",
        "checklist.reply_yes_no": "Please reply *Yes*, *No*, or *N/A*.",
        "checklist.shift_list": (
            "📋 *Your shift has {count} task(s):*\n\n{list}\n\n"
            "Reply *Yes*, *No*, or *N/A* for each task as I send them."
        ),
        "checklist.first_task": "Your shift has {total} task{plural}. Here's your first one:",
        "checklist.next_ack": "Got it. Next up:",
        "checklist.task_line": "*Task {index}/{total}:* {title}",
        "checklist.reply_prompt": "Reply *Yes*, *No*, or *N/A*.",
        "checklist.reply_prompt_photo": (
            "Reply *Yes*, *No*, or *N/A*. If you say Yes, I'll ask for a quick photo as proof."
        ),
        "checklist.photo_prompt": (
            "Great — please send a *photo* as proof for:\n\n*{title}*{desc}\n\n"
            "Open your camera and send the picture here, then I'll continue."
        ),
        "checklist.complete": (
            "Nice work — checklist complete! {yes} done, {no} still open, {na} skipped "
            "out of {total} tasks. Have a great shift!"
        ),
        "checklist.stopped": "Got it — checklist stopped after *{title}*.{note}",
        "checklist.flagged_named": " I've flagged this for {names}.",
        "checklist.flagged_generic": " I've flagged this for the assigned person.",
        "checklist.noted_next": "Noted for *{title}*.{suffix} Next up:",
        # PayGuard
        "payguard.title": "PayGuard — approval needed",
        "payguard.nudge": (
            "Hi {first}, *{req}* is waiting for your approval to pay an invoice of "
            "*{money}* to {vendor}{inv_no}.\n\n"
            "PayGuard · {rung}. Reply *approve payment* / *reject payment* "
            "(or *approuver le paiement* / *رفض الدفع*), or open Mizan to review."
        ),
        "payguard.nudge_reminder": (
            "Hi {first}, just a nudge — *{req}* is still waiting for your approval "
            "to pay an invoice of *{money}* to {vendor}{inv_no}.\n\n"
            "You're on *{rung}* of the PayGuard ladder. "
            "Reply *approve payment* or open Mizan → Finance to clear it."
        ),
        "payguard.off": "PayGuard is off — you can mark this invoice paid anytime.",
        "payguard.no_tier": "No PayGuard ladder for {currency} at this amount — clear to pay.",
        "payguard.started": (
            "PayGuard started for *{money}* ({tier}). "
            "Waiting on rung 1 — I've notified the approver(s)."
        ),
        "payguard.already": "This payment approval is already {status}.",
        "payguard.not_authorized": (
            "You're not on this PayGuard rung. "
            "Ask the assigned approver, or an owner, to clear it."
        ),
        "payguard.rejected": "Payment rejected for *{vendor}* ({money}).",
        "payguard.complete": "✓ PayGuard complete — *{money}* to {vendor} is cleared to pay.",
        "payguard.next_rung": (
            "✓ Rung {cleared} cleared. Now waiting on *{label}* (rung {current}/{total})."
        ),
        "payguard.need_first": "This bill needs PayGuard approval first. Ask me to start approval.",
        "payguard.still_waiting": "PayGuard is still waiting on an approver for this invoice.",
        "payguard.was_rejected": "Payment was rejected on the PayGuard ladder. Start a new approval to retry.",
        # Compliance docs
        "compliance.expiry.title": "Document expiry reminder",
        "compliance.expiry.when_ago": "expired {n} day(s) ago ({date})",
        "compliance.expiry.when_today": "expires *today* ({date})",
        "compliance.expiry.when_in": "expires in {n} day(s) ({date})",
        "compliance.expiry.body": (
            "📋 *{title}* {when}.\n"
            "Type: {doc_type}. Renew it so you don't miss compliance — reply to Miya when updated."
        ),
        "compliance.expiry.app": "{title} — {when_plain}",
    },
    "fr": {
        "time.minutes_from_now": "{n} minutes",
        "time.in_minutes": "dans {n} minutes",
        "shift.role.fallback": "PERSONNEL",
        "notify.shift.assigned.title": "Nouveau service attribué",
        "notify.shift.assigned.body": "Un nouveau service vous a été attribué le {start}.",
        "notify.shift.updated.title": "Service mis à jour",
        "notify.shift.updated.body": "Votre service du {start} a été mis à jour.",
        "notify.shift.cancelled.title": "Service annulé",
        "notify.shift.cancelled.body": "Votre service du {start} a été annulé.",
        "notify.shift.reminder.title": "Rappel de service",
        "notify.shift.reminder.body": "Rappel : vous avez un service qui commence bientôt le {start}.",
        "checklist.reminder": "Bonjour {name} ! Vous avez {count} tâches assignées pour votre service.\n\nAperçu : {preview}\n\nBon courage !",
        "checklist.preview.more": "...",
        "process.started.wa": (
            "📋 Nouveau processus pour vous : *{name}*\n\n"
            "Une fois pointé, dites *démarrer la checklist* (ou *start checklist* / *ابدأ المهام*) "
            "à Miya et je vous guide étape par étape."
        ),
        "checklist.none": "Aucune tâche ni checklist n’est assignée à votre service pour le moment. Vous êtes prêt !",
        "checklist.already_complete": "Votre checklist est déjà terminée. Bravo !",
        "checklist.already_complete_shift": "Votre checklist est déjà terminée. Bon service !",
        "checklist.need_clock_in": (
            "Vous devez pointer avant de démarrer la checklist. "
            "Pointez d’abord, puis demandez-moi de démarrer la checklist."
        ),
        "checklist.load_error": "Je n’arrive pas à charger votre checklist. Réessayez.",
        "checklist.no_active": "Vous n’avez pas de checklist active. Dites *démarrer la checklist* pour commencer.",
        "checklist.reply_yes_no": "Répondez *Oui*, *Non* ou *N/A*.",
        "checklist.shift_list": (
            "📋 *Votre service a {count} tâche(s) :*\n\n{list}\n\n"
            "Répondez *Oui*, *Non* ou *N/A* pour chaque tâche que je vous envoie."
        ),
        "checklist.first_task": "Votre service a {total} tâche{plural}. Voici la première :",
        "checklist.next_ack": "Noté. Ensuite :",
        "checklist.task_line": "*Tâche {index}/{total} :* {title}",
        "checklist.reply_prompt": "Répondez *Oui*, *Non* ou *N/A*.",
        "checklist.reply_prompt_photo": (
            "Répondez *Oui*, *Non* ou *N/A*. Si vous dites Oui, je demanderai une photo comme preuve."
        ),
        "checklist.photo_prompt": (
            "Parfait — envoyez une *photo* comme preuve pour :\n\n*{title}*{desc}\n\n"
            "Ouvrez l’appareil photo et envoyez l’image ici, puis je continue."
        ),
        "checklist.complete": (
            "Bravo — checklist terminée ! {yes} faites, {no} encore ouvertes, {na} ignorées "
            "sur {total} tâches. Bon service !"
        ),
        "checklist.stopped": "Compris — checklist arrêtée après *{title}*.{note}",
        "checklist.flagged_named": " J’ai signalé cela à {names}.",
        "checklist.flagged_generic": " J’ai signalé cela à la personne assignée.",
        "checklist.noted_next": "Noté pour *{title}*.{suffix} Ensuite :",
        "payguard.title": "PayGuard — approbation requise",
        "payguard.nudge": (
            "Salut {first}, *{req}* attend votre approbation pour payer une facture de "
            "*{money}* à {vendor}{inv_no}.\n\n"
            "PayGuard · {rung}. Répondez *approuver le paiement* / *refuser le paiement* "
            "(ou *approve payment*), ou ouvrez Mizan pour vérifier."
        ),
        "payguard.nudge_reminder": (
            "Salut {first}, petit rappel — *{req}* attend toujours votre approbation "
            "pour payer une facture de *{money}* à {vendor}{inv_no}.\n\n"
            "Vous êtes sur *{rung}* de l’échelle PayGuard. "
            "Répondez *approuver le paiement* ou ouvrez Mizan → Finance."
        ),
        "payguard.off": "PayGuard est désactivé — vous pouvez marquer cette facture comme payée.",
        "payguard.no_tier": "Aucune échelle PayGuard pour {currency} à ce montant — vous pouvez payer.",
        "payguard.started": (
            "PayGuard démarré pour *{money}* ({tier}). "
            "En attente de l’étape 1 — j’ai notifié le(s) approbateur(s)."
        ),
        "payguard.already": "Cette approbation de paiement est déjà {status}.",
        "payguard.not_authorized": (
            "Vous n’êtes pas sur cette étape PayGuard. "
            "Demandez à l’approbateur assigné ou à un propriétaire."
        ),
        "payguard.rejected": "Paiement refusé pour *{vendor}* ({money}).",
        "payguard.complete": "✓ PayGuard terminé — *{money}* pour {vendor} est prêt à être payé.",
        "payguard.next_rung": (
            "✓ Étape {cleared} validée. En attente de *{label}* (étape {current}/{total})."
        ),
        "payguard.need_first": "Cette facture nécessite d’abord une approbation PayGuard. Demandez-moi de démarrer l’approbation.",
        "payguard.still_waiting": "PayGuard attend encore un approbateur pour cette facture.",
        "payguard.was_rejected": "Le paiement a été refusé sur l’échelle PayGuard. Relancez une approbation pour réessayer.",
        "compliance.expiry.title": "Rappel d’expiration de document",
        "compliance.expiry.when_ago": "expiré il y a {n} jour(s) ({date})",
        "compliance.expiry.when_today": "expire *aujourd’hui* ({date})",
        "compliance.expiry.when_in": "expire dans {n} jour(s) ({date})",
        "compliance.expiry.body": (
            "📋 *{title}* {when}.\n"
            "Type : {doc_type}. Renouvelez-le pour rester conforme — répondez à Miya une fois mis à jour."
        ),
        "compliance.expiry.app": "{title} — {when_plain}",
    },
    "ar": {
        "time.minutes_from_now": "{n} دقيقة",
        "time.in_minutes": "بعد {n} دقيقة",
        "shift.role.fallback": "طاقم",
        "notify.shift.assigned.title": "تم تعيين وردية جديدة",
        "notify.shift.assigned.body": "تم تعيين وردية جديدة لك بتاريخ {start}.",
        "notify.shift.updated.title": "تم تحديث الوردية",
        "notify.shift.updated.body": "تم تحديث ورديتك بتاريخ {start}.",
        "notify.shift.cancelled.title": "تم إلغاء الوردية",
        "notify.shift.cancelled.body": "تم إلغاء ورديتك بتاريخ {start}.",
        "notify.shift.reminder.title": "تذكير بوردية قادمة",
        "notify.shift.reminder.body": "تذكير: لديك وردية ستبدأ قريبًا بتاريخ {start}.",
        "checklist.reminder": "مرحبًا {name}! لديك {count} مهام مخصصة لورديتك.\n\nمعاينة: {preview}\n\nبالتوفيق!",
        "checklist.preview.more": "...",
        "process.started.wa": (
            "📋 عملية جديدة لك: *{name}*\n\n"
            "بعد تسجيل الحضور، قل لـ ميّا *ابدأ المهام* (أو *start checklist* / *démarrer la checklist*) "
            "وسأرشدك خطوة بخطوة."
        ),
        "checklist.none": "لا توجد مهام أو قوائم تحقق لورديتك الآن. أنت جاهز!",
        "checklist.already_complete": "قائمة التحقق مكتملة بالفعل. أحسنت!",
        "checklist.already_complete_shift": "قائمة التحقق مكتملة بالفعل. وردية موفّقة!",
        "checklist.need_clock_in": (
            "يجب تسجيل الحضور قبل بدء قائمة التحقق. "
            "سجّل الحضور أولاً، ثم اطلب مني بدء قائمة التحقق."
        ),
        "checklist.load_error": "تعذّر تحميل قائمة التحقق. حاول مرة أخرى.",
        "checklist.no_active": "ليس لديك قائمة تحقق نشطة. قل *ابدأ المهام* للبدء.",
        "checklist.reply_yes_no": "يرجى الرد بـ *نعم* أو *لا* أو *غير منطبق*.",
        "checklist.shift_list": (
            "📋 *ورديتك فيها {count} مهمة/مهام:*\n\n{list}\n\n"
            "أجب بـ *نعم* أو *لا* أو *غير منطبق* لكل مهمة أرسلها لك."
        ),
        "checklist.first_task": "ورديتك فيها {total} مهمة/مهام. إليك الأولى:",
        "checklist.next_ack": "تم. التالية:",
        "checklist.task_line": "*المهمة {index}/{total}:* {title}",
        "checklist.reply_prompt": "أجب بـ *نعم* أو *لا* أو *غير منطبق*.",
        "checklist.reply_prompt_photo": (
            "أجب بـ *نعم* أو *لا* أو *غير منطبق*. إذا قلت نعم، سأطلب صورة كإثبات."
        ),
        "checklist.photo_prompt": (
            "ممتاز — أرسل *صورة* كإثبات لـ:\n\n*{title}*{desc}\n\n"
            "افتح الكاميرا وأرسل الصورة هنا، ثم أكمل."
        ),
        "checklist.complete": (
            "أحسنت — اكتملت قائمة التحقق! {yes} منجزة، {no} ما زالت مفتوحة، {na} متخطّاة "
            "من أصل {total}. وردية موفّقة!"
        ),
        "checklist.stopped": "حسناً — توقفت القائمة بعد *{title}*.{note}",
        "checklist.flagged_named": " تم إبلاغ {names}.",
        "checklist.flagged_generic": " تم إبلاغ الشخص المعيّن.",
        "checklist.noted_next": "تم تسجيل *{title}*.{suffix} التالية:",
        "payguard.title": "PayGuard — موافقة مطلوبة",
        "payguard.nudge": (
            "مرحباً {first}، *{req}* ينتظر موافقتك لدفع فاتورة بمبلغ "
            "*{money}* لـ {vendor}{inv_no}.\n\n"
            "PayGuard · {rung}. رد بـ *موافقة على الدفع* / *رفض الدفع* "
            "(أو *approve payment*)، أو افتح ميزان للمراجعة."
        ),
        "payguard.nudge_reminder": (
            "مرحباً {first}، تذكير — *{req}* ما زال ينتظر موافقتك "
            "لدفع فاتورة بمبلغ *{money}* لـ {vendor}{inv_no}.\n\n"
            "أنت على *{rung}* في سلم PayGuard. "
            "رد بـ *موافقة على الدفع* أو افتح ميزان → المالية."
        ),
        "payguard.off": "PayGuard متوقف — يمكنك تعليم الفاتورة كمدفوعة في أي وقت.",
        "payguard.no_tier": "لا يوجد سلم PayGuard لـ {currency} بهذا المبلغ — يمكنك الدفع.",
        "payguard.started": (
            "بدأ PayGuard لـ *{money}* ({tier}). "
            "بانتظار الدرجة 1 — تم إشعار الموافق(ين)."
        ),
        "payguard.already": "موافقة الدفع هذه بالفعل {status}.",
        "payguard.not_authorized": (
            "لست على هذه الدرجة في PayGuard. "
            "اطلب من الموافق المعيّن أو المالك."
        ),
        "payguard.rejected": "تم رفض الدفع لـ *{vendor}* ({money}).",
        "payguard.complete": "✓ اكتمل PayGuard — *{money}* لـ {vendor} جاهز للدفع.",
        "payguard.next_rung": (
            "✓ اكتملت الدرجة {cleared}. الآن بانتظار *{label}* (درجة {current}/{total})."
        ),
        "payguard.need_first": "هذه الفاتورة تحتاج موافقة PayGuard أولاً. اطلب مني بدء الموافقة.",
        "payguard.still_waiting": "PayGuard ما زال ينتظر موافقاً لهذه الفاتورة.",
        "payguard.was_rejected": "رُفض الدفع على سلم PayGuard. ابدأ موافقة جديدة للمحاولة مجدداً.",
        "compliance.expiry.title": "تذكير بانتهاء مستند",
        "compliance.expiry.when_ago": "انتهت صلاحيته منذ {n} يوم/أيام ({date})",
        "compliance.expiry.when_today": "تنتهي *اليوم* ({date})",
        "compliance.expiry.when_in": "تنتهي خلال {n} يوم/أيام ({date})",
        "compliance.expiry.body": (
            "📋 *{title}* {when}.\n"
            "النوع: {doc_type}. جدّده للبقاء ملتزماً — رد على ميّا بعد التحديث."
        ),
        "compliance.expiry.app": "{title} — {when_plain}",
    },
}


def tr(key: str, lang: str, **kwargs: Any) -> str:
    """
    Translate a key with safe English fallback and missing-key logging.
    """
    lang = normalize_language(lang)
    table = _CATALOG.get(lang) or _CATALOG["en"]
    text = table.get(key)
    if text is None:
        # log and fallback to english
        logger.warning("[i18n] Missing translation key '%s' for lang '%s'", key, lang)
        text = _CATALOG["en"].get(key, key)
    try:
        return text.format(**kwargs)
    except Exception:
        # If formatting fails, return raw text (still single-language)
        return text


def format_checklist_task_message(
    lang: str,
    *,
    title: str,
    index: int,
    total: int,
    description: str = "",
    requires_photo: bool = False,
    is_first: bool = False,
) -> str:
    """Localized Yes/No checklist step prompt for WhatsApp."""
    plural = "" if total == 1 else ("s" if normalize_language(lang) == "en" else "s")
    if normalize_language(lang) == "fr":
        plural = "" if total == 1 else "s"
    elif normalize_language(lang) == "ar":
        plural = ""
    head = (
        tr("checklist.first_task", lang, total=total, plural=plural)
        if is_first
        else tr("checklist.next_ack", lang)
    )
    lines = [head, "", tr("checklist.task_line", lang, index=index, total=total, title=title)]
    if (description or "").strip():
        lines.append(description.strip())
    lines.append("")
    lines.append(
        tr("checklist.reply_prompt_photo", lang)
        if requires_photo
        else tr("checklist.reply_prompt", lang)
    )
    return "\n".join(lines)


def format_photo_prompt(lang: str, *, title: str, description: str = "") -> str:
    desc = f"\n{description.strip()}" if (description or "").strip() else ""
    return tr("checklist.photo_prompt", lang, title=(title or "").strip() or "—", desc=desc)
