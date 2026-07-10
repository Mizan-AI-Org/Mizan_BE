import { ChatMessage, PreProcessor, UserDataInstance } from "lua-cli";
import ApiService from "../services/ApiService";
import { resolveAgentContext } from "../services/agentContext";
import {
    extractMizanUserIdFromLuaBridgeId,
    extractRestaurantIdFromLuaBridgeId,
} from "../utils/extractLuaBridgeContext";
import { resolveStaffPhoneForByPhoneTools } from "../utils/resolveStaffPhoneFromLuaUser";
import { resolveTenantForUser } from "../utils/resolveTenantForUser";
import {
    audienceContextLine,
    resolveMessageAudience,
} from "../utils/resolveMessageAudience";
import { stripSystemContextBlocks } from "../utils/stripSystemContext";

/** Bearer/header-safe string — rejects placeholders axios might choke on as Buffer.from(undefined). */
function coerceBearerLike(v: unknown): string | null {
    if (v === null || v === undefined) return null;
    if (typeof v === "string") {
        const t = v.trim();
        if (!t || t === "undefined" || t === "null") return null;
        return t;
    }
    if (typeof v === "number" && Number.isFinite(v)) {
        return String(v);
    }
    return null;
}

/**
 * Tenant Context Preprocessor
 * 
 * Validates that tenant context is available for multi-tenant operations.
 * 
 * APPROACH 1: runtimeContext (ACTIVE)
 * ------------------------------------
 * The tenant context comes from the frontend/API via runtimeContext field.
 * This is injected into the AI's prompt automatically - no preprocessing needed!
 * 
 * Frontend (LuaPop):
 *   window.LuaPop.init({
 *     agentId: "mizan-agent",
 *     sessionId: `mizan-${userId}`,
 *     runtimeContext: `Restaurant: ${restaurant.name} (ID: ${restaurant.id}), User: ${userName}, Role: ${role}`
 *   });
 * 
 * Backend (HTTP API):
 *   POST /chat/generate/mizan-agent
 *   { 
 *     messages: [...], 
 *     sessionId: "mizan-123",
 *     runtimeContext: "Restaurant: Baromètre (ID: barometre), User: Ahmed, Role: manager"
 *   }
 * 
 * The AI receives this context in its prompt and uses it when calling tools.
 * 
 * APPROACH 2: user.data (ALTERNATIVE - for persistence)
 * ------------------------------------------------------
 * If you need to persist tenant context on the user profile (e.g., set via webhook
 * when user first authenticates), the preprocessor can read from user.data.
 * See the commented-out section below.
 */
export const tenantContextPreprocessor = new PreProcessor({
    name: "tenant-context-validation",
    description: "Validates tenant context is available (context comes via runtimeContext)",
    priority: 100, // Run LATE in the pipeline to ensure our modifications stick

    execute: async (user: UserDataInstance, messages: ChatMessage[], channel: string) => {
        console.log("[TenantContext] V8 Processing request", {
            uid: user.uid,
            channel,
            hasUserData: !!user.data,
            dataKeys: user.data ? Object.keys(user.data).join(',') : 'none',
            hasLuaProfile: !!(user as any)._luaProfile
        });

        // 0. Extract token from Lua profile session data (passed via LuaPop.init({ token }))
        const luaProfile = (user as any)._luaProfile || {};
        console.log(`[TenantContext] Profile debug keys: ${Object.keys(luaProfile).join(', ')}`);

        const rawSessionToken =
            (user as any).token ||
            luaProfile.accessToken ||
            luaProfile.credentials?.accessToken ||
            luaProfile.token ||
            luaProfile.sessionToken;

        const sessionToken = coerceBearerLike(rawSessionToken);

        if (sessionToken && user.data?.token !== sessionToken) {
            console.log(`[TenantContext] 🔐 Syncing token to user.data and instance (length: ${sessionToken.length})`);
            user.data = { ...user.data, token: sessionToken };
            // Also sync directly to the user instance for maximum tool compatibility
            (user as any).token = sessionToken;
        } else if (!sessionToken) {
            console.warn(`[TenantContext] ⚠️ No sessionToken (accessToken/token/sessionToken) found in profile.`);
        }

        // 1. Detect context: user.data, Lua profile, and widget metadata (LuaPop.init({ metadata: { restaurantId } }))
        const metadata = luaProfile.metadata && typeof luaProfile.metadata === 'object' ? luaProfile.metadata : {};
        let detectedRestaurantId = user.data?.restaurantId || luaProfile.restaurantId || luaProfile.restaurant_id
            || (metadata as any).restaurantId || (metadata as any).restaurant_id;
        let detectedRestaurantName = user.data?.restaurantName || luaProfile.restaurantName || luaProfile.restaurant_name
            || (metadata as any).restaurantName || (metadata as any).restaurant_name;
        let detectedToken = coerceBearerLike(
            user.data?.token || (metadata as any).token || (metadata as any).accessToken,
        );
        if ((metadata as any).restaurantId || (metadata as any).restaurant_id) {
            console.log(`[TenantContext] 🏢 Restaurant from widget metadata: ${detectedRestaurantName} (${detectedRestaurantId})`);
        }

        // LuaPop / dashboard bridge encodes tenant in uid: …-tenant-<uuid>-user-<uuid>…
        if (!detectedRestaurantId) {
            const fromUid = extractRestaurantIdFromLuaBridgeId(user.uid);
            if (fromUid) {
                detectedRestaurantId = fromUid;
                console.log(`[TenantContext] 🏢 Restaurant from Lua bridge uid: ${fromUid}`);
            }
        }
        const metaUserId = (metadata as any).userId || (metadata as any).user_id;
        if (metaUserId) {
            const mid = String(metaUserId).trim();
            user.data = { ...user.data, userId: mid, mizanUserId: mid };
            console.log(`[TenantContext] 👤 Mizan user id from widget metadata: ${mid}`);
        } else if (!user.data?.mizanUserId) {
            const bridgeUserId =
                extractMizanUserIdFromLuaBridgeId(user.uid) ||
                extractMizanUserIdFromLuaBridgeId(luaProfile.sessionId) ||
                extractMizanUserIdFromLuaBridgeId((metadata as any).sessionId);
            if (bridgeUserId) {
                user.data = { ...user.data, userId: bridgeUserId, mizanUserId: bridgeUserId };
                console.log(`[TenantContext] 👤 Mizan user id from Lua bridge uid: ${bridgeUserId}`);
            }
        }
        const metaEmail =
            (metadata as any).email ||
            (metadata as any).emailAddress ||
            luaProfile.emailAddress ||
            luaProfile.email;
        if (metaEmail && !user.data?.email) {
            user.data = { ...user.data, email: String(metaEmail).trim() };
        }

        for (const msg of messages) {
            if (msg.type === 'text') {
                const text = msg.text;
                // Flexible regex for different formats. Any of the following in runtimeContext work:
                //   "Restaurant: Bistro (ID: abc123)"                 (canonical form, FE default)
                //   "RestaurantID: abc123"                            (legacy shorthand)
                //   "Workspace: Bistro | tenant_id ...: abc123"       (multi-vertical Mizan format)
                // If the canonical form is missing (older FE / external integrations) we fall back
                // to the Workspace/tenant_id pair so the preprocessor still anchors the tenant and
                // Miya doesn't reply "I don't have the restaurant context".
                const restaurantMatch =
                    text.match(/Restaurant:\s*([^(\n]+?)\s*\(ID:\s*([^)]+?)\)/i) ||
                    text.match(/RestaurantID:\s*([^,\n|]+)/i);
                const workspaceNameMatch = text.match(/Workspace:\s*([^|\n]+?)\s*(?:\||$)/i);
                const tenantIdMatch =
                    text.match(/tenant_id[^:]*:\s*([A-Za-z0-9\-]+)/i) ||
                    text.match(/restaurant_id:\s*([A-Za-z0-9\-]+)/i);
                const userMatch = text.match(/User:\s*([^(\n]+?)\s*\(ID:\s*([^)]+?)\)/i);
                const roleMatch = text.match(/Role:\s*([A-Za-z_]+)/i);
                // JWT tokens can be very long and may be followed by commas in runtimeContext
                // Match "Token: <jwt>" where jwt is base64url encoded (A-Za-z0-9-_ and .)
                // Capture until we hit a comma, closing paren, newline, or end of line
                const tokenMatch = text.match(/Token:\s*([A-Za-z0-9\-_\.]+(?:\.[A-Za-z0-9\-_\.]+)*)/i) ||
                    text.match(/accessToken:\s*([A-Za-z0-9\-_\.]+(?:\.[A-Za-z0-9\-_\.]+)*)/i);

                if (restaurantMatch) {
                    detectedRestaurantName = restaurantMatch[1].trim();
                    detectedRestaurantId = restaurantMatch[2] ? restaurantMatch[2].trim() : detectedRestaurantName;
                    console.log(`[TenantContext] 🏢 Detected Restaurant: ${detectedRestaurantName} (${detectedRestaurantId})`);
                } else if (tenantIdMatch) {
                    // Fallback path: FE's multi-vertical runtimeContext uses "Workspace: X | tenant_id ...: Y"
                    // without the literal "Restaurant: X (ID: Y)" prefix. Keep the agent working on that
                    // wire format by picking up the tenant id (+ workspace name when present) here.
                    detectedRestaurantId = tenantIdMatch[1].trim();
                    if (workspaceNameMatch) {
                        detectedRestaurantName = workspaceNameMatch[1].trim();
                    }
                    console.log(`[TenantContext] 🏢 Detected tenant via fallback: ${detectedRestaurantName || '(no name)'} (${detectedRestaurantId})`);
                }
                if (userMatch) {
                    const userName = userMatch[1].trim();
                    const userId = userMatch[2].trim();
                    console.log(`[TenantContext] 👤 Detected User: ${userName} (${userId})`);
                    user.data = { ...user.data, userName, userId, mizanUserId: userId };
                    const profile = (user as any)._luaProfile || {};
                    const meta =
                        profile.metadata && typeof profile.metadata === "object"
                            ? { ...(profile.metadata as Record<string, unknown>) }
                            : {};
                    meta.userId = userId;
                    meta.mizanUserId = userId;
                    (user as any)._luaProfile = { ...profile, metadata: meta };
                }
                if (roleMatch) {
                    const role = roleMatch[1].trim().toUpperCase();
                    console.log(`[TenantContext] 🎭 Detected Role: ${role}`);
                    user.data = { ...user.data, role };
                }
                if (tokenMatch && tokenMatch[1]) {
                    const t = tokenMatch[1].trim();
                    if (t && t !== "undefined" && t !== "null" && t.length > 50) {
                        // JWT tokens are typically 100+ characters, so this filters out false matches
                        detectedToken = t;
                        console.log(`[TenantContext] 🔑 Detected JWT Token from message (length: ${t.length})`);
                    }
                }
            }
        }

        if (detectedRestaurantId) user.data = { ...user.data, restaurantId: detectedRestaurantId, restaurantName: detectedRestaurantName };
        if (detectedToken) {
            user.data = { ...user.data, token: detectedToken };
            (user as any).token = detectedToken;
            console.log(`[TenantContext] ✅ Token saved to user.data.token (length: ${detectedToken.length})`);
        }

        // Always save token if we have it, even if restaurant isn't detected yet
        if (detectedToken && (!user.data?.token || user.data.token !== detectedToken)) {
            user.data = { ...user.data, token: detectedToken };
            (user as any).token = detectedToken;
            try {
                await user.save();
                console.log("[TenantContext] ✅ Token persisted immediately");
            } catch (e) {
                console.error("[TenantContext] ❌ Failed to persist token:", e);
            }
        }

        const now = new Date();
        const phone = resolveStaffPhoneForByPhoneTools(
            {
                uid: user.uid,
                data: (user.data || {}) as Record<string, unknown>,
                _luaProfile: (user as any)._luaProfile,
            },
            undefined,
        );

        // WhatsApp staff often have no persisted restaurantId until after activation.
        // Resolve tenant from phone / email / Mizan user id / Lua bridge session ids.
        if (!detectedRestaurantId) {
            try {
                const resolved = await resolveTenantForUser(user);
                if (resolved.restaurantId) {
                    detectedRestaurantId = resolved.restaurantId;
                    detectedRestaurantName =
                        resolved.restaurantName || detectedRestaurantName;
                    console.log(
                        `[TenantContext] 🏢 Restaurant resolved via resolveTenantForUser: ${detectedRestaurantName || ""} (${detectedRestaurantId})`,
                    );
                }
            } catch (e: unknown) {
                const em = e instanceof Error ? e.message : String(e);
                console.warn(`[TenantContext] resolveTenantForUser failed (non-fatal): ${em}`);
            }
        }

        // Legacy phone-only path (kept for explicit staff WhatsApp uids)
        if (!detectedRestaurantId && phone) {
            try {
                const lookup = await new ApiService().getStaffByPhoneForAgent(phone);
                if (lookup.success && lookup.found && lookup.staff?.restaurant_id) {
                    detectedRestaurantId = lookup.staff.restaurant_id;
                    detectedRestaurantName =
                        lookup.staff.restaurant_name || detectedRestaurantName;
                    user.data = {
                        ...user.data,
                        restaurantId: detectedRestaurantId,
                        restaurantName: detectedRestaurantName,
                        phone,
                    };
                    console.log(
                        `[TenantContext] 🏢 Restaurant resolved from phone: ${detectedRestaurantName} (${detectedRestaurantId})`,
                    );
                }
            } catch (e: any) {
                console.warn(
                    `[TenantContext] Phone→restaurant lookup failed (non-fatal): ${e?.message || e}`,
                );
            }
        }

        // 2. If we have context, inject/update the anchoring block
        if (detectedRestaurantId) {
            console.log(`[TenantContext] ⚓ Anchoring context for ${detectedRestaurantName} (${detectedRestaurantId})`);

            // Always attempt to save if user.data exists and was likely modified
            if (user.data && Object.keys(user.data).length > 0) {
                try {
                    await user.save();
                    console.log("[TenantContext] ✅ User context persisted.");
                } catch (e) {
                    console.error("[TenantContext] ❌ Failed to persist context:", e);
                }
            }

            // Load sector playbook so Miya is brilliantly vertical-aware on WhatsApp too
            let businessVertical =
                String(
                    (user.data as any)?.businessVertical ||
                        (user.data as any)?.business_vertical ||
                        (metadata as any).businessVertical ||
                        (metadata as any).business_vertical ||
                        "",
                )
                    .trim()
                    .toUpperCase() || "";
            let verticalHint = "";
            try {
                const details = await new ApiService().getRestaurantDetailsForAgent(
                    String(detectedRestaurantId),
                );
                if (details) {
                    businessVertical = String(
                        details.business_vertical ||
                            details.general_settings?.business_vertical ||
                            businessVertical ||
                            "RESTAURANT",
                    ).toUpperCase();
                    const pb = details.vertical_playbook;
                    if (pb) {
                        verticalHint = [
                            `Sector label: ${pb.label || businessVertical}`,
                            pb.vocabulary ? `Vocabulary: ${pb.vocabulary}` : null,
                            pb.priorities ? `Priorities: ${pb.priorities}` : null,
                            pb.do_not ? `Hard rules: ${pb.do_not}` : null,
                        ]
                            .filter(Boolean)
                            .join(" | ");
                    }
                    if (details.name && !detectedRestaurantName) {
                        detectedRestaurantName = details.name;
                    }
                    user.data = {
                        ...user.data,
                        businessVertical,
                        restaurantName: detectedRestaurantName || user.data?.restaurantName,
                    };
                }
            } catch (e: unknown) {
                const em = e instanceof Error ? e.message : String(e);
                console.warn(`[TenantContext] vertical playbook fetch failed (non-fatal): ${em}`);
            }
            if (!businessVertical) businessVertical = "RESTAURANT";

            const messageAudience = resolveMessageAudience(channel);
            const contextBlock = [
                `[SYSTEM: PERSISTENT CONTEXT]`,
                `Workspace: ${detectedRestaurantName || "Unknown"}`,
                `Restaurant: ${detectedRestaurantName || "Unknown"}`,
                `Restaurant ID: ${detectedRestaurantId}`,
                `business_vertical: ${businessVertical}`,
                verticalHint ? `VERTICAL_PLAYBOOK: ${verticalHint}` : null,
                `NOTE: restaurant_id is the tenant/workspace id for EVERY sector (retail, construction, healthcare ops, hospitality, manufacturing, services, restaurant, other) — not proof this is a restaurant.`,
                `User: ${user.data?.userName || user._luaProfile?.fullName || "Manager"} (Role: ${user.data?.role || "Owner"})`,
                user.data?.userId ? `Mizan User ID: ${user.data.mizanUserId || user.data.userId}` : null,
                user.data?.email ? `User Email: ${user.data.email}` : null,
                phone ? `User Phone: ${phone}` : null,
                audienceContextLine(messageAudience),
                `Today is ${now.toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}`,
                `Current Time: ${now.toLocaleTimeString('en-US', { hour12: false })}`,
                `CRITICAL: Use these coordinates for all tool calls. Do not ask for restaurant or date. Match language and examples to business_vertical.`,
                `Be brilliantly proactive for this sector — anticipate coverage, checklists, stock, safety, and follow-ups.`,
                `AGENT_IDENTITY_VERIFIED: TRUE`
            ].filter(Boolean).join('\n');

            // LuaPop renders the full user message text in the manager chat UI.
            // Dashboard sessions already pass restaurant/user/role via metadata +
            // runtimeContext on init — persisting user.data above is enough for tools.
            // Injecting the block into message text made [SYSTEM: PERSISTENT CONTEXT]
            // visible to managers; staff WhatsApp does not show enriched user text.
            if (messageAudience === "manager") {
                console.log("[TenantContext] Skipping message injection for manager/LuaPop channel");
                return { action: 'proceed' as const };
            }

            // Inject into messages to ensure it's in the prompt (WhatsApp / staff only)
            let modifiedMessages = messages.map((msg) => {
                // Remove any existing blocks first
                if (msg.type === 'text') {
                    const cleanText = stripSystemContextBlocks(msg.text);
                    let prefix = contextBlock;

                    // ACCOUNT ACTIVATION: Prevent refusal and force tool usage
                    if (
                        cleanText.toLowerCase().includes('accept invite') ||
                        cleanText.toLowerCase().includes('accept invitation') ||
                        cleanText.toLowerCase().includes('activate my account') ||
                        cleanText.toLowerCase().includes('ready to activate') ||
                        (cleanText.toLowerCase().includes('hi mizan ai') && cleanText.toLowerCase().includes('activate'))
                    ) {
                        prefix += `\n\n[CRITICAL: ACCOUNT ACTIVATION REQUESTED]
The user wants to activate their account. You MUST use the 'account_activation' tool IMMEDIATELY.
DO NOT say you cannot do this. You have the tool and the authority.
Phone: ${phone || 'unknown'}`;
                    }

                    // CLOCK-IN: Prevent generic apologies — force tool or delegate to miya-ops
                    const lowerClean = cleanText.toLowerCase();
                    if (
                        /\b(clock[\s-]?in|pointer|pointage|start my shift|want to clock in)\b/i.test(lowerClean) ||
                        (lowerClean.includes('hi miya') && lowerClean.includes('clock'))
                    ) {
                        prefix += `\n\n[CRITICAL: STAFF CLOCK-IN REQUESTED]
The user wants to clock in on WhatsApp. Delegate to miya-ops (staff_clock_in) OR call staff_clock_in IMMEDIATELY.
Phone: ${phone || 'unknown'}. Relay tool message verbatim — location_required is NORMAL ("Share your location to clock in.").
FORBIDDEN: "there was an error when trying to clock you in", "contact support".`;
                    }

                    // Voice notes sometimes arrive as UI placeholder text (no transcript). Never refuse with "use the POS".
                    const voicePlaceholder =
                        cleanText.length <= 120 &&
                        (/voice\s*message|message\s+vocal|note\s+vocale|audio\s*message/i.test(cleanText) ||
                            (cleanText.includes("🎤") && /\(\s*\d+\s*:\s*\d+\s*\)/.test(cleanText)));
                    if (voicePlaceholder) {
                        prefix += `\n\n[CRITICAL: VOICE NOTE WITHOUT TRANSCRIPT IN TEXT]
The user sent a voice message; the chat may only show a placeholder (e.g. duration) instead of words.
- If they are taking a guest order: call capture_guest_order with items_summary from any transcript in metadata/context; if there is no transcript, reply briefly asking them to type the order — NEVER say you cannot create orders or to use the POS.
- Staff orders are logged via capture_guest_order (Today's Orders), not the electronic POS.`;
                    }

                    // Inject into EVERY text message for maximum context retention in long conversations
                    return { ...msg, text: `${prefix}\n\n${cleanText}` };
                }
                return msg;
            });

            // When user sends only photo/voice (no text), inject context so report_incident has restaurant/phone
            const hasNoTextInTurn = !messages.some((m) => m.type === 'text');
            if (hasNoTextInTurn) {
                modifiedMessages = [{ type: 'text' as const, text: contextBlock } as ChatMessage, ...modifiedMessages];
            }

            return { action: 'proceed' as const, modifiedMessage: modifiedMessages };
        }

        // No restaurant context (e.g. new WhatsApp user activating) – still inject phone for activation flows
        const lastText = messages.filter((m) => m.type === 'text').slice(-1)[0]?.text || '';
        const isActivationMessage = (
            lastText.toLowerCase().includes('activate my account') ||
            lastText.toLowerCase().includes('ready to activate') ||
            lastText.toLowerCase().includes('hi mizan ai')
        );
        if (phone && isActivationMessage) {
            console.log(`[TenantContext] 📱 Activation flow (no restaurant): injecting phone ${phone}`);
            const activationBlock = `[CRITICAL: STAFF ACTIVATION REQUESTED]
The user wants to activate their account. Restaurant context is CRITICAL:
- The user is identified by their WhatsApp number (phone: ${phone}).
- Use the 'account_activation' tool IMMEDIATELY with phone: ${phone}.
- The tool looks up the pending activation record by phone and ties the user to the correct restaurant.
- DO NOT say you cannot do this. You HAVE the tool. Pass phone: ${phone}.`;
            const modifiedMessages = messages.map((m) =>
                m.type === 'text' ? { ...m, text: `${activationBlock}\n\n${m.text}` } : m
            );
            return { action: 'proceed' as const, modifiedMessage: modifiedMessages };
        }

        // No restaurant context but have phone — inject minimal context for media-only messages
        // (photos/voice for incident reports, etc.) so tools can resolve staff by phone
        if (phone) {
            const hasNoTextInTurn = !messages.some((m) => m.type === 'text');
            if (hasNoTextInTurn) {
                console.log(`[TenantContext] 📱 Media-only message (no restaurant): injecting phone ${phone}`);
                const phoneBlock = [
                    `[SYSTEM: PARTIAL CONTEXT]`,
                    `User Phone: ${phone}`,
                    `Today is ${now.toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}`,
                    `Current Time: ${now.toLocaleTimeString('en-US', { hour12: false })}`,
                    `The user sent a photo or voice message. If this looks like an incident report, use report_incident immediately.`,
                    `Restaurant will be auto-resolved from the phone number by the tool.`,
                ].join('\n');
                const modifiedMessages = [{ type: 'text' as const, text: phoneBlock } as ChatMessage, ...messages];
                return { action: 'proceed' as const, modifiedMessage: modifiedMessages };
            }
        }

        return { action: 'proceed' as const };

        // ═══════════════════════════════════════════════════════════════════════
        // APPROACH 2: user.data persistence (COMMENTED OUT)
        // ═══════════════════════════════════════════════════════════════════════
        // 
        // Use this approach when:
        // - You want tenant context persisted on the user profile
        // - Context is set once via webhook (userAuthWebhook) when user logs in
        // - PreProcessor reads from user.data and injects into messages
        //
        // To enable: uncomment the code below and comment out the 'proceed' above

        /*
        const restaurantId = user.data?.restaurantId;
        const restaurantName = user.data?.restaurantName;
        const userRole = user.data?.role;

        if (restaurantId) {
            console.log(`[TenantContext] ✅ Found context: ${restaurantName} (${restaurantId})`);
            
            // Inject context into the first message for the agent
            const enrichedMessages = injectTenantContext(messages, {
                restaurantId,
                restaurantName: restaurantName || "Unknown Restaurant",
                role: userRole
            });

            return { action: 'proceed' as const, modifiedMessage: enrichedMessages };
        }

        // No tenant context - decide how to handle
        console.warn("[TenantContext] ⚠️ No tenant context on user.data");
        
        // Option A: Block unauthenticated users
        // return {
        //     action: 'block' as const,
        //     response: "Please log in through the Mizan app to access restaurant features."
        // };

        // Option B: Proceed without context (let agent handle it)
        return { action: 'proceed' as const };
        */
    }
});

/**
 * Helper: Injects tenant context into message text (for Approach 2)
 */
function injectTenantContext(
    messages: ChatMessage[],
    context: { restaurantId: string; restaurantName: string; role?: string }
): ChatMessage[] {
    const contextBlock = [
        `[TENANT CONTEXT]`,
        `Restaurant: ${context.restaurantName}`,
        `Restaurant ID: ${context.restaurantId}`,
        context.role ? `User Role: ${context.role}` : null
    ].filter(Boolean).join('\n');

    return messages.map((msg, index) => {
        // Only inject into the first text message
        if (index === 0 && msg.type === 'text') {
            return {
                ...msg,
                text: `${msg.text}\n\n${contextBlock}`
            };
        }
        return msg;
    });
}

export default tenantContextPreprocessor;
