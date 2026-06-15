import axios from "axios";
import { env } from "lua-cli";
/** Normalize tokens/keys read from env or JWT slots — avoids axios/internal Buffer.from(undefined). */
function coerceBearerLike(v) {
    if (v === null || v === undefined)
        return null;
    if (typeof v === "string") {
        const t = v.trim();
        if (!t || t === "undefined" || t === "null")
            return null;
        return t;
    }
    if (typeof v === "number" && Number.isFinite(v)) {
        return String(v);
    }
    return null;
}
/** Pick auth for Mizan agent endpoints: user JWT resolves restaurant from token; else use agent key. */
function agentAuthHeaders(userToken) {
    const tok = coerceBearerLike(userToken);
    if (tok) {
        return { Authorization: `Bearer ${tok}` };
    }
    const rawAgent = env("LUA_WEBHOOK_API_KEY") || env("WEBHOOK_API_KEY") || env("MIZAN_SERVICE_TOKEN");
    const agentKey = coerceBearerLike(rawAgent ?? "");
    if (!agentKey)
        throw new Error("No agent key or user token configured");
    return { Authorization: `Bearer ${agentKey}` };
}
/**
 * Authorization for Django routes that ONLY accept Bearer settings.LUA_WEBHOOK_API_KEY
 * (e.g. /api/timeclock/agent/clock-in-by-phone/, /api/agent/account-activation/).
 * Never pass a user JWT here — the backend compares the header to the single shared agent key.
 */
function mizanLuaWebhookAuthorization() {
    const k = coerceBearerLike(env("LUA_WEBHOOK_API_KEY") || env("WEBHOOK_API_KEY") || env("MIZAN_SERVICE_TOKEN") || "");
    if (!k)
        return null;
    return { Authorization: `Bearer ${k}` };
}
/** Drop undefined/null header values — Node/axios may call Buffer.from(undefined) otherwise. */
function dropUndefinedHeaders(h) {
    const out = {};
    if (!h)
        return out;
    for (const [key, val] of Object.entries(h)) {
        if (val === undefined || val === null)
            continue;
        const s = typeof val === "string" ? val : String(val);
        if (s.trim() !== "")
            out[key] = s;
    }
    return out;
}
/** Match Django `normalize_activation_phone_inbound` (Morocco national → 212…). */
function normalizeActivationPhoneDigits(raw) {
    const d = String(raw ?? "")
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
function agentAuthHeadersWithRestaurant(restaurantId, userToken) {
    const headers = agentAuthHeaders(userToken);
    // Never assign undefined/empty to X-Restaurant-Id — axios/Node can throw Buffer.from(undefined).
    const rid = restaurantId !== undefined && restaurantId !== null ? String(restaurantId).trim() : "";
    if (rid)
        headers["X-Restaurant-Id"] = rid;
    return headers;
}
/** Bearer agent key + X-Restaurant-Id only when restaurant id is non-empty (avoids undefined header values). */
function agentKeyBearerHeadersWithRestaurant(agentKey, restaurantId) {
    const k = (agentKey || "").trim();
    const headers = { Authorization: `Bearer ${k}` };
    const rid = restaurantId !== undefined && restaurantId !== null ? String(restaurantId).trim() : "";
    if (rid)
        headers["X-Restaurant-Id"] = rid;
    return headers;
}
export default class ApiService {
    constructor() {
        const rawBase = env("API_BASE_URL") || process.env.API_BASE_URL || "http://localhost:8000";
        this.baseUrl =
            typeof rawBase === "string" && rawBase.trim().length > 0 ? rawBase.trim() : "http://localhost:8000";
        console.log(`[ApiService] Initialized with baseUrl: ${this.baseUrl}`);
        this.timeout = 10000;
        this.axiosInstance = axios.create({
            baseURL: this.baseUrl,
            timeout: this.timeout,
            headers: {
                "Content-Type": "application/json",
                "User-Agent": "Lua-Skill/1.0",
            },
        });
        // Axios + Node may call Buffer on header values; undefined/null throws
        // "The first argument must be of type string or an instance of Buffer...".
        // Axios 1.x often uses AxiosHeaders — prefer forEach + delete for correctness.
        this.axiosInstance.interceptors.request.use((config) => {
            const h = config?.headers;
            if (!h || typeof h !== "object")
                return config;
            try {
                if (typeof h.forEach === "function") {
                    const toDelete = [];
                    h.forEach((value, key) => {
                        if (value === undefined || value === null)
                            toDelete.push(key);
                    });
                    for (const key of toDelete) {
                        if (typeof h.delete === "function")
                            h.delete(key);
                        else
                            delete h[key];
                    }
                }
                else {
                    for (const key of Object.keys(h)) {
                        if (h[key] === undefined || h[key] === null) {
                            delete h[key];
                        }
                    }
                }
            }
            catch {
                /* ignore */
            }
            return config;
        });
    }
    async validateUser(token) {
        try {
            const response = await this.axiosInstance.get("/api/auth/agent-context/", {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });
            return {
                isValid: true,
                user: response.data.user,
                restaurant: response.data.restaurant
            };
        }
        catch (error) {
            console.error("[ApiService] Token validation failed:", error.message);
            return {
                isValid: false,
                error: error.message
            };
        }
    }
    async fetchUserData(userId) {
        try {
            const response = await this.axiosInstance.get("/api/auth/agent-context/", {
                headers: agentAuthHeaders(null),
                params: { userId }
            });
            return {
                id: userId,
                name: response.data.user?.first_name || 'Unknown',
                status: 'success',
                timestamp: new Date().toISOString()
            };
        }
        catch (error) {
            return {
                id: userId,
                name: 'Unknown',
                status: 'error',
                error: error.message,
                timestamp: new Date().toISOString()
            };
        }
    }
    // Scheduling Methods
    async getStaffList(restaurantId, token) {
        try {
            const response = await this.axiosInstance.get("/api/staff/", {
                headers: { 'Authorization': `Bearer ${token}` },
                params: { restaurant_id: restaurantId }
            });
            return response.data;
        }
        catch (error) {
            const msg = error?.response?.data?.error || error?.response?.data?.detail || error?.message || "Unknown error";
            console.error("[ApiService] Failed to fetch staff list:", msg);
            throw new Error(`Failed to fetch staff list: ${msg}`);
        }
    }
    /**
     * Get staff list using agent key or user JWT.
     * When userToken is provided, backend resolves restaurant from JWT (fixes "problem resolving restaurant context").
     *
     * Group-targeting: pass `tags` (canonical vocab: KITCHEN, SERVICE,
     * FRONT_OFFICE, BACK_OFFICE, PURCHASES, CONTROL, ADMINISTRATION,
     * MANAGEMENT, HOUSEKEEPING, MARKETING) or `department` to narrow
     * the list to a specific crew. Backend does any-of matching on
     * tags, case-insensitive exact on department.
     */
    async getStaffListForAgent(restaurantId, name, userToken, role, opts) {
        const params = {};
        if (name)
            params.name = name;
        if (role)
            params.role = role;
        if (restaurantId)
            params.restaurant_id = restaurantId;
        if (opts?.tags && opts.tags.length > 0) {
            params.tags = opts.tags.join(",");
        }
        if (opts?.department) {
            params.department = Array.isArray(opts.department) ? opts.department.join(",") : opts.department;
        }
        try {
            const response = await this.axiosInstance.get("/api/scheduling/agent/staff/", {
                headers: agentAuthHeadersWithRestaurant(restaurantId, userToken),
                params
            });
            const data = response.data;
            if (Array.isArray(data))
                return data;
            return Array.isArray(data?.results) ? data.results : [];
        }
        catch (error) {
            const status = error?.response?.status;
            // Retry with agent key when user token is rejected (expired/invalid JWT)
            if (userToken && (status === 401 || status === 403 || status === 400)) {
                console.warn("[ApiService] User token failed for staff list, retrying with agent key...");
                try {
                    const response = await this.axiosInstance.get("/api/scheduling/agent/staff/", {
                        headers: agentAuthHeadersWithRestaurant(restaurantId, null),
                        params
                    });
                    const data = response.data;
                    if (Array.isArray(data))
                        return data;
                    return Array.isArray(data?.results) ? data.results : [];
                }
                catch (retryError) {
                    const retryMsg = retryError?.response?.data?.error || retryError?.response?.data?.detail || retryError?.message || "Unknown error";
                    console.error("[ApiService] Agent key retry also failed:", retryMsg);
                    throw new Error(`Could not retrieve staff list: ${retryMsg}`);
                }
            }
            const msg = error?.response?.data?.error || error?.response?.data?.detail || error?.message || "Unknown error";
            console.error("[ApiService] Failed to fetch staff list (agent auth):", msg);
            throw new Error(`Could not retrieve staff list: ${msg}`);
        }
    }
    /**
     * Get staff count only (for "how many staff?"). Uses user JWT when provided so backend resolves restaurant.
     */
    async getStaffCountForAgent(restaurantId, userToken) {
        const params = restaurantId ? { restaurant_id: restaurantId } : {};
        const parseResponse = (data) => ({
            count: data?.count ?? 0,
            message: data?.message ?? `There are ${data?.count ?? 0} staff members.`,
            by_role: data?.by_role,
            restaurant_name: data?.restaurant_name
        });
        try {
            const response = await this.axiosInstance.get("/api/scheduling/agent/staff-count/", {
                headers: agentAuthHeadersWithRestaurant(restaurantId, userToken),
                params
            });
            return parseResponse(response.data);
        }
        catch (error) {
            const status = error?.response?.status;
            if (userToken && (status === 401 || status === 403 || status === 400)) {
                console.warn("[ApiService] User token failed for staff count, retrying with agent key...");
                try {
                    const response = await this.axiosInstance.get("/api/scheduling/agent/staff-count/", {
                        headers: agentAuthHeadersWithRestaurant(restaurantId, null),
                        params
                    });
                    return parseResponse(response.data);
                }
                catch (retryError) {
                    const retryMsg = retryError?.response?.data?.error || retryError?.response?.data?.detail || retryError?.message || "Unknown error";
                    console.error("[ApiService] Agent key retry also failed:", retryMsg);
                    throw new Error(`Could not retrieve staff count: ${retryMsg}`);
                }
            }
            const msg = error?.response?.data?.error || error?.response?.data?.detail || error?.message || "Unknown error";
            console.error("[ApiService] Failed to fetch staff count:", msg);
            throw new Error(`Could not retrieve staff count: ${msg}`);
        }
    }
    /**
     * Create a shift. When userToken is provided, backend resolves restaurant from JWT.
     * When using agent key, sends X-Restaurant-Id so backend can resolve even if body is not parsed.
     */
    async createShiftForAgent(data, userToken) {
        try {
            const response = await this.axiosInstance.post("/api/scheduling/agent/create-shift/", data, {
                headers: agentAuthHeadersWithRestaurant(data.restaurant_id, userToken)
            });
            return response.data;
        }
        catch (error) {
            const status = error?.response?.status;
            // Only retry auth failures (401/403), NOT 400 (validation errors) — retrying
            // validation errors just floods the backend with identical bad requests
            if (userToken && (status === 401 || status === 403)) {
                console.warn("[ApiService] User token auth failed for create shift, retrying with agent key...");
                try {
                    const response = await this.axiosInstance.post("/api/scheduling/agent/create-shift/", data, {
                        headers: agentAuthHeadersWithRestaurant(data.restaurant_id, null)
                    });
                    return response.data;
                }
                catch (retryError) {
                    console.error("[ApiService] Agent key retry also failed:", retryError.message);
                    if (retryError.response?.data) {
                        throw new Error(retryError.response.data.error || JSON.stringify(retryError.response.data));
                    }
                    throw new Error(`Failed to create shift: ${retryError.message}`);
                }
            }
            console.error("[ApiService] Failed to create shift (status=%s):", status, error.message);
            if (status === 409) {
                throw error;
            }
            if (error.response && error.response.data) {
                throw new Error(error.response.data.error || JSON.stringify(error.response.data));
            }
            throw new Error(`Failed to create shift: ${error.message}`);
        }
    }
    /**
     * Create shifts for ALL staff with a given role on specified dates.
     * Use for "schedule all waiters on March 4 and 5 from 6 to 8pm".
     */
    async createShiftsByRoleForAgent(data, userToken) {
        const attempt = async (token) => {
            try {
                const response = await this.axiosInstance.post("/api/scheduling/agent/create-shifts-by-role/", data, { headers: agentAuthHeadersWithRestaurant(data.restaurant_id, token) });
                return { ok: true, data: response.data };
            }
            catch (error) {
                const status = error?.response?.status;
                const body = error?.response?.data || {};
                return { ok: false, status, body, message: error.message };
            }
        };
        let result = await attempt(userToken);
        if (!result.ok && userToken && (result.status === 401 || result.status === 403)) {
            result = await attempt(null);
        }
        if (result.ok) {
            return result.data;
        }
        // Surface the full 409 body so the tool can present each per-(staff, date)
        // conflict to the manager and ask for confirmation before retrying with force.
        if (result.status === 409) {
            return {
                success: false,
                error: result.body.error || "Scheduling conflicts detected.",
                conflicts: result.body.conflicts || [],
                total_planned: result.body.total_planned,
                total_conflicts: result.body.total_conflicts,
                can_force: result.body.can_force === true,
                status_code: 409,
            };
        }
        return {
            success: false,
            error: result.body?.error || result.message || "Failed to create shifts.",
            status_code: result.status,
        };
    }
    /**
     * List task templates for the restaurant.
     * Used by Miya to assign tasks/processes to shifts (e.g. "assign the opening checklist").
     */
    async getTaskTemplatesForAgent(restaurantId, userToken) {
        try {
            const headers = agentAuthHeadersWithRestaurant(restaurantId, userToken);
            const response = await this.axiosInstance.get("/api/scheduling/agent/task-templates/", {
                headers,
                params: { restaurant_id: restaurantId },
            });
            return response.data;
        }
        catch (error) {
            const msg = error?.response?.data?.error || error?.response?.data?.detail || error?.message || "Unknown error";
            console.error("[ApiService] Failed to fetch task templates (agent auth):", msg);
            throw new Error(`Could not retrieve task templates: ${msg}`);
        }
    }
    /**
     * Create a task template for the restaurant.
     * Used by Miya when a requested template doesn't exist - Miya can create the perfect template for that shift.
     */
    async createTaskTemplateForAgent(data, userToken) {
        try {
            const headers = agentAuthHeadersWithRestaurant(data.restaurant_id, userToken);
            const response = await this.axiosInstance.post("/api/scheduling/agent/create-task-template/", data, { headers });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to create task template (agent auth):", error.message);
            const err = error?.response?.data?.error || error.message;
            return { success: false, error: err };
        }
    }
    /**
     * Attach task templates to an existing shift.
     * Used by Miya when manager says "add the opening checklist to Maria's shift".
     */
    async attachTemplatesToShiftForAgent(data, userToken) {
        try {
            const headers = agentAuthHeadersWithRestaurant(data.restaurant_id, userToken);
            const response = await this.axiosInstance.post("/api/scheduling/agent/attach-templates-to-shift/", data, { headers });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to attach templates to shift (agent auth):", error.message);
            const err = error?.response?.data?.error || error.message;
            return { success: false, error: err };
        }
    }
    /**
     * Get assigned shifts. When userToken is provided, backend resolves restaurant from JWT.
     */
    async getAssignedShiftsForAgent(params, userToken) {
        const { restaurant_id, ...restParams } = params;
        const allParams = { ...restParams, restaurant_id };
        try {
            const headers = agentAuthHeadersWithRestaurant(restaurant_id, userToken);
            const response = await this.axiosInstance.get("/api/scheduling/agent/list-shifts/", {
                headers,
                params: allParams
            });
            return response.data;
        }
        catch (error) {
            const httpStatus = error?.response?.status;
            if (userToken && (httpStatus === 401 || httpStatus === 403 || httpStatus === 400)) {
                console.warn("[ApiService] User token failed for list shifts, retrying with agent key...");
                try {
                    const response = await this.axiosInstance.get("/api/scheduling/agent/list-shifts/", {
                        headers: agentAuthHeadersWithRestaurant(restaurant_id, null),
                        params: allParams
                    });
                    return response.data;
                }
                catch (retryError) {
                    const msg = retryError?.response?.data?.error || retryError?.response?.data?.detail || retryError?.message || "Unknown error";
                    console.error("[ApiService] Agent key retry for list shifts also failed:", msg);
                    return { results: [], error: msg };
                }
            }
            const msg = error?.response?.data?.error || error?.response?.data?.detail || error?.message || "Unknown error";
            console.error("[ApiService] Failed to fetch assigned shifts (agent auth):", msg);
            return { results: [], error: msg };
        }
    }
    /**
     * Get attendance report using agent key authentication.
     */
    async getAttendanceReport(restaurantId, date) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey) {
            console.error("[ApiService] No agent key configured");
            return { date: date || '', summary: [] };
        }
        try {
            const response = await this.axiosInstance.get("/api/timeclock/agent/attendance-report/", {
                headers: {
                    'Authorization': `Bearer ${agentKey}`
                },
                params: {
                    restaurant_id: restaurantId,
                    date: date
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to fetch attendance report (agent auth):", error.message);
            return { date: date || '', summary: [] };
        }
    }
    /**
     * Send WhatsApp notification about a shift.
     */
    async sendShiftNotification(data) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey) {
            throw new Error("No agent key configured");
        }
        try {
            const response = await this.axiosInstance.post("/api/scheduling/agent/notify-shift/", data, {
                headers: {
                    'Authorization': `Bearer ${agentKey}`
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to send shift notification:", error.message);
            return { success: false, error: error.message };
        }
    }
    /**
     * Generate optimized schedule for a week. When userToken is provided, backend resolves restaurant from JWT.
     */
    async optimizeScheduleForAgent(data, userToken) {
        try {
            const headers = { ...agentAuthHeaders(userToken) };
            if (!userToken && data.restaurant_id) {
                const rid = String(data.restaurant_id).trim();
                if (rid)
                    headers["X-Restaurant-Id"] = rid;
            }
            // Optimize can take 1–2 min for many staff; use 2 min so success response is received
            const response = await this.axiosInstance.post("/api/scheduling/agent/optimize-schedule/", data, {
                timeout: 120000,
                headers
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to optimize schedule (agent auth):", error.message);
            if (error.code === "ECONNABORTED" || (error.message && String(error.message).toLowerCase().includes("timeout"))) {
                throw new Error("TIMEOUT");
            }
            if (error.response && error.response.data) {
                throw new Error(error.response.data.error || JSON.stringify(error.response.data));
            }
            throw new Error(`Failed to optimize schedule: ${error.message}`);
        }
    }
    async getStaffProfiles(restaurantId, token) {
        try {
            const response = await this.axiosInstance.get("/api/staff/", {
                headers: {
                    'Authorization': `Bearer ${token}`
                },
                params: {
                    restaurant_id: restaurantId,
                    include_profile: true
                }
            });
            return response.data;
        }
        catch (error) {
            const status = error.response?.status;
            const data = error.response?.data;
            console.error(`[ApiService] Failed to fetch staff profiles: ${status} ${JSON.stringify(data || error.message)}`);
            throw new Error(`API Error ${status || 'Unknown'}: ${data?.detail || data?.error || error.message}`);
        }
    }
    async getAssignedShifts(params, token) {
        try {
            const response = await this.axiosInstance.get("/api/scheduling/assigned-shifts-v2/", {
                headers: {
                    'Authorization': `Bearer ${token}`
                },
                params: params
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to fetch assigned shifts:", error.message);
            throw new Error(`Failed to fetch shifts: ${error.message}`);
        }
    }
    async createAssignedShift(data, token) {
        try {
            const response = await this.axiosInstance.post("/api/scheduling/assigned-shifts-v2/", data, {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to create shift:", error.message);
            // Return error details if available from backend
            if (error.response && error.response.data) {
                throw new Error(`Failed to create shift: ${JSON.stringify(error.response.data)}`);
            }
            throw new Error(`Failed to create shift: ${error.message}`);
        }
    }
    async updateAssignedShift(shiftId, data, token) {
        try {
            const response = await this.axiosInstance.patch(`/api/scheduling/assigned-shifts-v2/${shiftId}/`, data, {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to update shift:", error.message);
            if (error.response && error.response.data) {
                throw new Error(`Failed to update shift: ${JSON.stringify(error.response.data)}`);
            }
            throw new Error(`Failed to update shift: ${error.message}`);
        }
    }
    async detectConflicts(params) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey) {
            throw new Error("No agent key configured");
        }
        try {
            const response = await this.axiosInstance.get("/api/scheduling/agent/detect-conflicts/", {
                headers: {
                    'Authorization': `Bearer ${agentKey}`
                },
                params: params
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to detect conflicts:", error.message);
            return { has_conflicts: false, error: error.message };
        }
    }
    async optimizeSchedule(data, token) {
        try {
            const response = await this.axiosInstance.post("/api/scheduling/auto-schedule/", data, {
                timeout: this.timeout * 2, // Optimization might take longer
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to optimize schedule:", error.message);
            if (error.response && error.response.data) {
                throw new Error(`Optimization failed: ${JSON.stringify(error.response.data)}`);
            }
            throw new Error(`Optimization failed: ${error.message}`);
        }
    }
    // Restaurant Context
    async getRestaurantDetails(restaurantId, token) {
        try {
            const response = await this.axiosInstance.get(`/api/restaurants/${restaurantId}/`, {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to fetch restaurant details:", error.message);
            return null;
        }
    }
    async getRestaurantDetailsForAgent(restaurantId) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey) {
            console.error("[ApiService] No agent key configured");
            return null;
        }
        try {
            const response = await this.axiosInstance.get("/api/scheduling/agent/restaurant-details/", {
                headers: {
                    'Authorization': `Bearer ${agentKey}`
                },
                params: { restaurant_id: restaurantId }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to fetch restaurant details (agent auth):", error.message);
            return null;
        }
    }
    /**
     * Search restaurants by name (agent key). Lets Miya resolve "Barometre" / "Mizan Mistro"
     * when the user has no session token (e.g. lua chat from CLI).
     */
    async getRestaurantsSearchForAgent(name) {
        try {
            const headers = agentAuthHeaders(null);
            const response = await this.axiosInstance.get("/api/scheduling/agent/restaurant-search/", {
                headers,
                params: { name: name.trim() }
            });
            const data = response.data;
            const list = Array.isArray(data?.results) ? data.results : [];
            return list.map((r) => ({ id: r.id, name: r.name || r.id }));
        }
        catch (error) {
            console.error("[ApiService] Restaurant search (agent) failed:", error.message);
            return [];
        }
    }
    async getOperationalAdvice(restaurantId, date) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey) {
            console.error("[ApiService] No agent key configured");
            return null;
        }
        try {
            const response = await this.axiosInstance.get("/api/scheduling/agent/operational-advice/", {
                headers: {
                    'Authorization': `Bearer ${agentKey}`
                },
                params: {
                    restaurant_id: restaurantId,
                    date: date
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to fetch operational advice:", error.message);
            return null;
        }
    }
    /**
     * List or save agent memories (preferences, corrections, facts) for the restaurant.
     * Used by Miya for context persistence and learning from corrections.
     */
    async getMemoriesForAgent(restaurantId, options, userToken) {
        try {
            const headers = agentAuthHeadersWithRestaurant(restaurantId, userToken);
            const response = await this.axiosInstance.get("/api/scheduling/agent/memories/", {
                headers,
                params: userToken ? (options || {}) : { restaurant_id: restaurantId, ...(options || {}) },
            });
            const data = response.data;
            return { memories: data?.memories || [] };
        }
        catch (error) {
            console.error("[ApiService] Failed to fetch memories (agent):", error.message);
            return { memories: [] };
        }
    }
    async saveMemoryForAgent(data, userToken) {
        try {
            const headers = agentAuthHeadersWithRestaurant(data.restaurant_id, userToken);
            const response = await this.axiosInstance.post("/api/scheduling/agent/memories/", data, { headers });
            return response.data;
        }
        catch (error) {
            const err = error?.response?.data?.error || error.message;
            console.error("[ApiService] Failed to save memory (agent):", err);
            return { success: false, error: err };
        }
    }
    async deleteMemoryForAgent(restaurantId, payload, userToken) {
        try {
            const headers = agentAuthHeadersWithRestaurant(restaurantId, userToken);
            const response = await this.axiosInstance.post("/api/scheduling/agent/memories/delete/", payload, {
                headers,
                params: restaurantId ? { restaurant_id: restaurantId } : {},
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to delete memory (agent):", error.message);
            return { success: false };
        }
    }
    /**
     * Proactive insights: no-shows, understaffed shifts, late patterns, staffing suggestions.
     * Miya uses this to surface alerts and recommendations without being asked.
     */
    async getProactiveInsightsForAgent(restaurantId, date, userToken) {
        try {
            const headers = agentAuthHeadersWithRestaurant(restaurantId, userToken);
            const response = await this.axiosInstance.get("/api/scheduling/agent/proactive-insights/", {
                headers,
                params: { date: date || new Date().toISOString().split("T")[0] },
            });
            const data = response.data;
            return {
                insights: data?.insights || [],
                has_alerts: !!data?.has_alerts,
                date: data?.date || "",
            };
        }
        catch (error) {
            console.error("[ApiService] Failed to fetch proactive insights:", error.message);
            return { insights: [], has_alerts: false, date: date || "" };
        }
    }
    // Inventory Methods
    async getInventoryItems(restaurantId, token) {
        try {
            const response = await this.axiosInstance.get("/api/inventory/items/", {
                headers: {
                    'Authorization': `Bearer ${token}`
                },
                params: { restaurant_id: restaurantId }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to fetch inventory items:", error.message);
            return [];
        }
    }
    /** List inventory items (agent key + X-Restaurant-Id). */
    async getInventoryItemsForAgent(restaurantId) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey) {
            console.error("[ApiService] No agent key configured");
            return { items: [], count: 0, restaurant_id: restaurantId };
        }
        try {
            const response = await this.axiosInstance.get("/api/inventory/agent/items/", {
                headers: agentKeyBearerHeadersWithRestaurant(agentKey, restaurantId),
                params: { restaurant_id: restaurantId },
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to fetch inventory (agent):", error.message);
            return { items: [], count: 0, restaurant_id: restaurantId };
        }
    }
    /** Parse schedule from photo (base64). Returns { template_name, shifts } or error. */
    async parseSchedulePhotoForAgent(base64Image, contentType, restaurantId) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            throw new Error("No agent key configured");
        const headers = agentKeyBearerHeadersWithRestaurant(agentKey, restaurantId);
        try {
            const response = await this.axiosInstance.post("/api/scheduling/agent/parse-schedule-photo/", {
                base64_image: base64Image,
                content_type: contentType || "image/jpeg",
            }, { headers });
            return response.data;
        }
        catch (error) {
            const err = error.response?.data?.detail || error.message;
            return { shifts: [], error: err };
        }
    }
    /** Parse schedule from document (base64 + filename). */
    async parseScheduleDocumentForAgent(base64Content, filename, restaurantId) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            throw new Error("No agent key configured");
        const headers = agentKeyBearerHeadersWithRestaurant(agentKey, restaurantId);
        try {
            const response = await this.axiosInstance.post("/api/scheduling/agent/parse-schedule-document/", {
                base64_content: base64Content,
                filename: filename,
            }, { headers });
            return response.data;
        }
        catch (error) {
            const err = error.response?.data?.detail || error.message;
            return { shifts: [], error: err };
        }
    }
    /** Apply parsed schedule (template + shifts to week or save as template). */
    async applyParsedScheduleForAgent(restaurantId, body) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            throw new Error("No agent key configured");
        try {
            const response = await this.axiosInstance.post("/api/scheduling/agent/apply-parsed-schedule/", {
                ...body,
                restaurant_id: restaurantId,
            }, {
                headers: agentKeyBearerHeadersWithRestaurant(agentKey, restaurantId),
            });
            return { success: true, ...response.data };
        }
        catch (error) {
            const err = error.response?.data?.detail || error.message;
            return { success: false, error: err };
        }
    }
    /** Request labor/attendance report export (PDF or Excel). Returns response with binary; caller may use as download. */
    async getAttendanceExportForAgent(restaurantId, startDate, endDate, format = 'excel') {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey) {
            return { success: false, error: "No agent key configured" };
        }
        try {
            const response = await this.axiosInstance.get("/api/reporting/agent/attendance-export/", {
                headers: agentKeyBearerHeadersWithRestaurant(agentKey, restaurantId),
                params: { restaurant_id: restaurantId, start_date: startDate, end_date: endDate, format: format === 'xlsx' ? 'excel' : format },
                responseType: 'arraybuffer',
            });
            const ext = format === 'pdf' ? 'pdf' : 'xlsx';
            const filename = `staff_attendance_report_${startDate}_${endDate}.${ext}`;
            return { success: true, data: response.data, filename };
        }
        catch (error) {
            const err = error.response?.data ? (typeof error.response.data === 'string' ? error.response.data : (error.response.data.detail || JSON.stringify(error.response.data))) : error.message;
            return { success: false, error: err };
        }
    }
    /**
     * Send formal announcement (app + WhatsApp) to an audience.
     *
     * Audience supports `tags` (canonical staff-tag vocabulary — see
     * `accounts.staff_tags`), `departments` (free-text dept string on
     * StaffProfile), `roles`, and explicit `staff_ids`. When multiple
     * filters are supplied the backend OR-joins them.
     */
    async sendAnnouncementForAgent(restaurantId, message, options) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey) {
            return { success: false, error: "No agent key configured" };
        }
        try {
            const response = await this.axiosInstance.post("/api/notifications/agent/announcement/", {
                restaurant_id: restaurantId,
                message: message,
                title: options?.title || "Announcement",
                audience: options?.audience ?? "all",
                sender_id: options?.sender_id,
            }, {
                headers: { 'Authorization': `Bearer ${agentKey}` },
            });
            return response.data;
        }
        catch (error) {
            const err = error.response?.data?.error || error.message;
            return { success: false, error: err };
        }
    }
    /**
     * Create a dashboard.Task row (the "Tasks & Demands" widget) and, in
     * the same call, optionally send the assigned staff member a WhatsApp
     * message with the task details.
     *
     * Backend: POST /api/dashboard/agent/tasks/create/
     * Auth:    Bearer LUA_WEBHOOK_API_KEY (agent key)
     *
     * Exactly one assignee field must be non-empty. The backend prefers
     * `user_id` → `email` → `phone` → fuzzy `name` within the workspace.
     */
    async createDashboardTaskForAgent(restaurantId, input) {
        const agentKey = env("LUA_WEBHOOK_API_KEY") || env("WEBHOOK_API_KEY") || env("MIZAN_SERVICE_TOKEN");
        if (!agentKey) {
            return { success: false, error: "No agent key configured" };
        }
        const rid = restaurantId !== undefined && restaurantId !== null ? String(restaurantId).trim() : "";
        if (!rid) {
            return {
                success: false,
                error: "Missing restaurant_id",
                message_for_user: "I couldn't tell which restaurant this task belongs to. Open Miya from your Mizan dashboard and try again.",
            };
        }
        try {
            const response = await this.axiosInstance.post("/api/dashboard/agent/tasks/create/", {
                restaurant_id: rid,
                title: input.title,
                description: input.description,
                priority: input.priority,
                due_date: input.due_date,
                ai_summary: input.ai_summary,
                notify_whatsapp: input.notify_whatsapp,
                whatsapp_message: input.whatsapp_message,
                user_id: input.user_id,
                email: input.email,
                phone: input.phone,
                name: input.name,
                category: input.category,
                request_deadline_from_staff: input.request_deadline_from_staff,
                require_read_receipt: input.require_read_receipt,
                attachments: input.attachments,
                follow_up_enabled: input.follow_up_enabled,
                follow_up_max: input.follow_up_max,
            }, {
                headers: agentKeyBearerHeadersWithRestaurant(agentKey, rid),
            });
            return response.data;
        }
        catch (error) {
            const body = error.response?.data;
            const err = body?.message_for_user ||
                body?.error ||
                error.message ||
                "Could not create task";
            return { success: false, error: err };
        }
    }
    /**
     * Reassign an existing dashboard.Task (Tasks & Demands) and WhatsApp the new assignee.
     * Backend: POST /api/dashboard/agent/tasks/reassign/
     */
    async reassignDashboardTaskForAgent(restaurantId, input) {
        const agentKey = env("LUA_WEBHOOK_API_KEY") || env("WEBHOOK_API_KEY") || env("MIZAN_SERVICE_TOKEN");
        if (!agentKey) {
            return { success: false, error: "No agent key configured" };
        }
        const rid = restaurantId !== undefined && restaurantId !== null ? String(restaurantId).trim() : "";
        if (!rid) {
            return {
                success: false,
                error: "Missing restaurant_id",
                message_for_user: "I couldn't tell which restaurant this task belongs to. Open Miya from your Mizan dashboard and try again.",
            };
        }
        try {
            const response = await this.axiosInstance.post("/api/dashboard/agent/tasks/reassign/", {
                restaurant_id: rid,
                task_id: input.task_id,
                notify_whatsapp: input.notify_whatsapp,
                whatsapp_message: input.whatsapp_message,
                note: input.note,
                user_id: input.user_id,
                email: input.email,
                phone: input.phone,
                name: input.name,
            }, {
                headers: agentKeyBearerHeadersWithRestaurant(agentKey, rid),
            });
            return response.data;
        }
        catch (error) {
            const body = error.response?.data;
            const err = body?.message_for_user ||
                body?.error ||
                error.message ||
                "Could not reassign task";
            return { success: false, error: err };
        }
    }
    /**
     * Create a tenant-wide dashboard widget category ("rubrique") for grouping shortcuts.
     * Backend: POST /api/dashboard/agent/categories/create/
     */
    async createDashboardCategoryForAgent(restaurantId, input) {
        const agentKey = env("LUA_WEBHOOK_API_KEY") || env("WEBHOOK_API_KEY") || env("MIZAN_SERVICE_TOKEN");
        if (!agentKey) {
            return { success: false, error: "No agent key configured" };
        }
        const rid = restaurantId !== undefined && restaurantId !== null ? String(restaurantId).trim() : "";
        if (!rid) {
            return {
                success: false,
                error: "Missing restaurant_id",
                message_for_user: "I couldn't tell which workspace this category belongs to. Open Miya from your logged-in Mizan dashboard and try again.",
            };
        }
        const nm = (input.name || "").trim();
        if (!nm) {
            return { success: false, error: "name is required", message_for_user: "I need a name for the new dashboard section." };
        }
        try {
            const response = await this.axiosInstance.post("/api/dashboard/agent/categories/create/", {
                restaurant_id: rid,
                name: nm.slice(0, 100),
                order_index: input.order_index ?? 0,
                user_id: input.user_id,
                email: input.email,
                phone: input.phone,
            }, {
                headers: agentKeyBearerHeadersWithRestaurant(agentKey, rid),
            });
            return response.data;
        }
        catch (error) {
            const body = error.response?.data;
            const err = body?.message_for_user ||
                body?.error ||
                error.message ||
                "Could not create category";
            return { success: false, error: err, message_for_user: body?.message_for_user };
        }
    }
    /**
     * Dashboard widget management for Miya (agent key auth).
     *
     * Covers list / add / remove / reorder for built-in widgets and
     * create / delete for custom tiles. Each method returns the backend's
     * raw payload including `success`, `order`, and `message_for_user` so
     * callers can relay it verbatim.
     */
    async manageDashboardWidgetsForAgent(restaurantId, action, input) {
        const agentKey = env("LUA_WEBHOOK_API_KEY") ||
            env("WEBHOOK_API_KEY") ||
            env("MIZAN_SERVICE_TOKEN");
        if (!agentKey) {
            return { success: false, error: "No agent key configured" };
        }
        const rid = restaurantId !== undefined && restaurantId !== null ? String(restaurantId).trim() : "";
        if (!rid) {
            return {
                success: false,
                error: "Missing restaurant_id",
                message_for_user: "I couldn't tell which restaurant this dashboard is for. Open Miya from your logged-in Mizan dashboard (or ensure your workspace is linked), then try again.",
            };
        }
        const pathByAction = {
            list: "/api/dashboard/agent/widgets/list/",
            add: "/api/dashboard/agent/widgets/add/",
            remove: "/api/dashboard/agent/widgets/remove/",
            reorder: "/api/dashboard/agent/widgets/reorder/",
            create_custom: "/api/dashboard/agent/widgets/create/",
            delete_custom: "/api/dashboard/agent/widgets/custom/delete/",
        };
        if (action === "create_category") {
            return this.createDashboardCategoryForAgent(restaurantId, {
                name: (input.category_name || "").trim(),
                order_index: typeof input.order_index === "number" ? input.order_index : 0,
                user_id: input.user_id,
                email: input.email,
                phone: input.phone,
            });
        }
        try {
            const response = await this.axiosInstance.post(pathByAction[action], {
                restaurant_id: rid,
                user_id: input.user_id,
                email: input.email,
                phone: input.phone,
                widgets: input.widgets,
                order: input.order,
                title: input.title,
                subtitle: input.subtitle,
                link_url: input.link_url,
                icon: input.icon,
                add_to_dashboard: input.add_to_dashboard,
                category_id: input.category_id,
                category_name: input.category_name,
                widget_id: input.widget_id,
            }, {
                headers: agentKeyBearerHeadersWithRestaurant(agentKey, rid),
            });
            return response.data;
        }
        catch (error) {
            const body = error.response?.data;
            const err = body?.message_for_user ||
                body?.error ||
                error.message ||
                "Could not complete widget action";
            return { success: false, error: err };
        }
    }
    /** Generate standalone tasks from a task template (due date + optional assignees). */
    async generateTasksFromTemplateForAgent(restaurantId, templateId, dueDate, assignedTo) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey) {
            return { success: false, error: "No agent key configured" };
        }
        try {
            const response = await this.axiosInstance.post("/api/scheduling/agent/generate-tasks/", {
                restaurant_id: restaurantId,
                template_id: templateId,
                due_date: dueDate,
                assigned_to: assignedTo || [],
            }, {
                headers: agentKeyBearerHeadersWithRestaurant(agentKey, restaurantId),
            });
            return response.data;
        }
        catch (error) {
            const err = error.response?.data?.error || error.message;
            return { success: false, error: err };
        }
    }
    /** Run recurring task generation for active templates. */
    async runRecurringTasksForAgent(restaurantId, options) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey) {
            return { success: false, error: "No agent key configured" };
        }
        try {
            const response = await this.axiosInstance.post("/api/scheduling/agent/run-recurring/", {
                restaurant_id: restaurantId,
                frequency: options?.frequency,
                date: options?.date,
            }, {
                headers: agentKeyBearerHeadersWithRestaurant(agentKey, restaurantId),
            });
            return response.data;
        }
        catch (error) {
            const err = error.response?.data?.error || error.message;
            return { success: false, error: err };
        }
    }
    /** Get staff profile report PDF (agent). Returns binary. */
    async getStaffReportPdfForAgent(restaurantId, staffId) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey) {
            return { success: false, error: "No agent key configured" };
        }
        try {
            const response = await this.axiosInstance.get("/api/agent/staff-report-pdf/", {
                headers: agentKeyBearerHeadersWithRestaurant(agentKey, restaurantId),
                params: { restaurant_id: restaurantId, staff_id: staffId },
                responseType: 'arraybuffer',
            });
            return { success: true, data: response.data, filename: `staff_report_${staffId}.pdf` };
        }
        catch (error) {
            const err = error.response?.data ? (typeof error.response.data === 'string' ? error.response.data : (error.response.data.detail || JSON.stringify(error.response.data))) : error.message;
            return { success: false, error: err };
        }
    }
    // Checklist Methods
    async getShiftChecklists(token) {
        try {
            const response = await this.axiosInstance.get("/api/checklists/shift-checklists/", {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to fetch shift checklists:", error.message);
            return { checklists: [], error: error.message };
        }
    }
    async getChecklistsForAgent(staffId) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey) {
            console.error("[ApiService] No agent key configured");
            return { checklists: [], error: "No agent key configured" };
        }
        try {
            const response = await this.axiosInstance.get("/api/checklists/agent/shift-checklists/", {
                headers: {
                    'Authorization': `Bearer ${agentKey}`
                },
                params: { staff_id: staffId }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to fetch shift checklists (agent auth):", error.message);
            return { checklists: [], error: error.message };
        }
    }
    async initiateChecklistForAgent(staffId) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey) {
            console.error("[ApiService] No agent key configured");
            return { status: "error", message: "No agent key configured" };
        }
        try {
            const response = await this.axiosInstance.post("/api/checklists/agent/initiate/", { staff_id: staffId }, {
                headers: {
                    'Authorization': `Bearer ${agentKey}`
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to initiate checklist (agent auth):", error.message);
            return { status: "error", message: error.message };
        }
    }
    async createChecklistExecution(data, token) {
        try {
            const response = await this.axiosInstance.post("/api/checklists/executions/", {
                template: data.template_id,
                assigned_shift: data.assigned_shift_id
            }, {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to create checklist execution:", error.message);
            if (error.response && error.response.data) {
                throw new Error(`Failed to create execution: ${JSON.stringify(error.response.data)}`);
            }
            throw new Error(`Failed to create execution: ${error.message}`);
        }
    }
    async startChecklistExecution(executionId, token) {
        try {
            const response = await this.axiosInstance.post(`/api/checklists/executions/${executionId}/start/`, {}, {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to start checklist execution:", error.message);
            throw new Error(`Failed to start execution: ${error.message}`);
        }
    }
    async syncChecklistResponse(executionId, data, token) {
        try {
            const response = await this.axiosInstance.post(`/api/checklists/executions/${executionId}/sync/`, data, {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to sync checklist response:", error.message);
            if (error.response && error.response.data) {
                throw new Error(`Sync failed: ${JSON.stringify(error.response.data)}`);
            }
            throw new Error(`Sync failed: ${error.message}`);
        }
    }
    async syncChecklistResponseForAgent(executionId, data) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey) {
            console.error("[ApiService] No agent key configured");
            throw new Error("No agent key configured");
        }
        try {
            const response = await this.axiosInstance.post(`/api/checklists/agent/executions/${executionId}/sync/`, data, {
                headers: {
                    'Authorization': `Bearer ${agentKey}`
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to sync checklist response (agent auth):", error.message);
            if (error.response && error.response.data) {
                throw new Error(`Sync failed: ${JSON.stringify(error.response.data)}`);
            }
            throw new Error(`Sync failed: ${error.message}`);
        }
    }
    async completeChecklistExecution(executionId, completionNotes, token) {
        try {
            const response = await this.axiosInstance.post(`/api/checklists/executions/${executionId}/complete/`, {
                completion_notes: completionNotes
            }, {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to complete checklist:", error.message);
            if (error.response && error.response.data) {
                throw new Error(`Completion failed: ${JSON.stringify(error.response.data)}`);
            }
            throw new Error(`Completion failed: ${error.message}`);
        }
    }
    async completeChecklistExecutionForAgent(executionId, completionNotes) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey) {
            console.error("[ApiService] No agent key configured");
            throw new Error("No agent key configured");
        }
        try {
            const response = await this.axiosInstance.post(`/api/checklists/agent/executions/${executionId}/complete/`, {
                completion_notes: completionNotes
            }, {
                headers: {
                    'Authorization': `Bearer ${agentKey}`
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to complete checklist (agent auth):", error.message);
            if (error.response && error.response.data) {
                throw new Error(`Completion failed: ${JSON.stringify(error.response.data)}`);
            }
            throw new Error(`Completion failed: ${error.message}`);
        }
    }
    // Communication Methods
    async sendWhatsapp(data, token) {
        try {
            const response = await this.axiosInstance.post("/api/notifications/agent/send-whatsapp/", data, {
                headers: {
                    'Authorization': `Bearer ${token}` // Agent key
                }
            });
            return response.data;
        }
        catch (error) {
            const data = error.response?.data;
            let detail = (typeof data?.error === 'string' && data.error) ||
                (data?.error && typeof data.error === 'object' && data.error.message) ||
                data?.detail ||
                data?.message;
            if (!detail && typeof data === 'string')
                detail = data;
            const msg = detail || error.message || 'WhatsApp send failed';
            console.error("[ApiService] Failed to send WhatsApp:", msg, data || '');
            return {
                success: false,
                error: msg,
                provider_response: data,
                http_status: error.response?.status,
            };
        }
    }
    async clockIn(data, token) {
        try {
            const response = await this.axiosInstance.post("/api/timeclock/agent/clock-in/", data, {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to clock in:", error.message);
            return { success: false, error: error.message };
        }
    }
    /** Clock-in by phone (for Miya when staff say "clock in" in chat). Optional lat/lon for geofence validation. */
    async clockInByPhone(data, _tokenLegacy) {
        try {
            const auth = mizanLuaWebhookAuthorization();
            if (!auth) {
                console.error("[ApiService] clockInByPhone: no LUA_WEBHOOK_API_KEY / WEBHOOK_API_KEY / MIZAN_SERVICE_TOKEN");
                return {
                    success: false,
                    error: "No agent key configured",
                    message_for_user: "Clock-in is temporarily unavailable. Please contact your manager — the assistant is not linked to the server.",
                };
            }
            const phoneDigits = normalizeActivationPhoneDigits(String(data.phone ?? "").replace(/\D/g, "").trim());
            if (!phoneDigits || phoneDigits.length < 6) {
                return {
                    success: false,
                    error: "Invalid or missing phone",
                    message_for_user: "We couldn't find your account. Please contact your manager to be added.",
                };
            }
            const payload = { phone: phoneDigits };
            const lat = data.latitude;
            const lng = data.longitude;
            if (typeof lat === "number" &&
                typeof lng === "number" &&
                Number.isFinite(lat) &&
                Number.isFinite(lng)) {
                payload.latitude = lat;
                payload.longitude = lng;
            }
            const headers = dropUndefinedHeaders({
                ...auth,
            });
            const response = await this.axiosInstance.post("/api/timeclock/agent/clock-in-by-phone/", payload, { headers, timeout: 30000, validateStatus: () => true });
            return response.data;
        }
        catch (error) {
            const data = error.response?.data;
            const msg = data?.message_for_user || data?.error || error.message;
            console.error("[ApiService] clockInByPhone failed:", msg);
            if (error.response?.status === 401) {
                return {
                    success: false,
                    error: "Unauthorized",
                    message_for_user: "We couldn't verify the clock-in service. Please ask your manager to check that Miya is configured with the same API key as the Mizan server.",
                };
            }
            return {
                success: false,
                error: data?.error || error.message,
                message_for_user: data?.message_for_user || "Something went wrong. Please try again or contact your manager.",
            };
        }
    }
    async clockOut(data, token) {
        try {
            const response = await this.axiosInstance.post("/api/timeclock/agent/clock-out/", data, {
                headers: { 'Authorization': `Bearer ${token}` }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to clock out:", error.message);
            return { success: false, error: error.message };
        }
    }
    async clockOutByPhone(phone, _tokenLegacy) {
        try {
            const auth = mizanLuaWebhookAuthorization();
            if (!auth) {
                return {
                    success: false,
                    error: "No agent key configured",
                    message_for_user: "Clock-out is temporarily unavailable. Please contact your manager — the assistant is not linked to the server.",
                };
            }
            const phoneDigits = normalizeActivationPhoneDigits(String(phone ?? "").replace(/\D/g, "").trim());
            const headers = dropUndefinedHeaders({
                ...auth,
            });
            const response = await this.axiosInstance.post("/api/timeclock/agent/clock-out-by-phone/", { phone: phoneDigits }, { headers, timeout: 30000 });
            return response.data;
        }
        catch (error) {
            const d = error.response?.data;
            if (error.response?.status === 401) {
                return {
                    success: false,
                    error: "Unauthorized",
                    message_for_user: "We couldn't verify the clock-out service. Please ask your manager to check that Miya is configured with the same API key as the Mizan server.",
                };
            }
            return {
                success: false,
                error: d?.error || error.message,
                message_for_user: d?.message_for_user || "Something went wrong. Please try again.",
            };
        }
    }
    /** Start the WhatsApp step-by-step checklist for a staff member by phone (e.g. when staff say "start checklist"). */
    async startWhatsAppChecklistByPhone(phone, token) {
        try {
            const response = await this.axiosInstance.post("/api/notifications/agent/start-whatsapp-checklist/", { phone }, { headers: agentAuthHeaders(token) });
            return response.data;
        }
        catch (error) {
            const d = error.response?.data;
            return {
                success: false,
                error: d?.error || error.message,
                message_for_user: d?.message_for_user ||
                    "I encountered an issue while trying to start the checklist. Please try again later or contact support if the problem persists.",
            };
        }
    }
    async respondToChecklist(phone, response, token, notes) {
        try {
            const resp = await this.axiosInstance.post("/api/notifications/agent/checklist/respond/", { phone, response, notes }, { headers: agentAuthHeaders(token) });
            return resp.data;
        }
        catch (error) {
            const d = error.response?.data;
            return {
                success: false,
                error: d?.error || error.message,
                message_for_user: d?.message_for_user || "Could not record your response. Please try again.",
            };
        }
    }
    async previewChecklistByPhone(phone, token) {
        try {
            const response = await this.axiosInstance.post("/api/notifications/agent/preview-checklist/", { phone }, { headers: agentAuthHeaders(token) });
            return response.data;
        }
        catch (error) {
            const d = error.response?.data;
            return {
                success: false,
                error: d?.error || error.message,
                message_for_user: d?.message_for_user || "I couldn't load your checklist preview right now. Please try again.",
            };
        }
    }
    async markNoShow(shiftId, token, restaurantId) {
        try {
            const response = await this.axiosInstance.post("/api/scheduling/agent/mark-no-show/", { shift_id: shiftId, restaurant_id: restaurantId }, { headers: agentAuthHeaders(token) });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.response?.data?.error || error.message };
        }
    }
    async assignCoverage(shiftId, staffId, token, restaurantId) {
        try {
            const response = await this.axiosInstance.post("/api/scheduling/agent/assign-coverage/", { shift_id: shiftId, staff_id: staffId, restaurant_id: restaurantId }, { headers: agentAuthHeaders(token) });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.response?.data?.error || error.message };
        }
    }
    async listStaffRequests(token, restaurantId, statusFilter = 'PENDING') {
        try {
            const response = await this.axiosInstance.get("/api/staff/agent/requests/", { params: { restaurant_id: restaurantId, status: statusFilter }, headers: agentAuthHeaders(token) });
            return response.data;
        }
        catch (error) {
            return { success: false, requests: [], error: error.response?.data?.error || error.message };
        }
    }
    async approveStaffRequest(requestId, token, restaurantId) {
        try {
            const response = await this.axiosInstance.post("/api/staff/agent/requests/approve/", { request_id: requestId, restaurant_id: restaurantId }, { headers: agentAuthHeaders(token) });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.response?.data?.error || error.message };
        }
    }
    async rejectStaffRequest(requestId, token, reason, restaurantId) {
        try {
            const response = await this.axiosInstance.post("/api/staff/agent/requests/reject/", { request_id: requestId, reason, restaurant_id: restaurantId }, { headers: agentAuthHeaders(token) });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.response?.data?.error || error.message };
        }
    }
    /** Park a staff request as WAITING_ON an external dependency. */
    async waitOnStaffRequest(restaurantId, requestId, data) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            return { success: false, error: "No agent key configured" };
        try {
            const response = await this.axiosInstance.post(`/api/staff/requests/${requestId}/wait-on/`, { reason: data.waiting_reason, follow_up_date: data.follow_up_date }, { headers: agentKeyBearerHeadersWithRestaurant(agentKey, restaurantId) });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.response?.data?.error || error.message };
        }
    }
    async requestTimeOff(data, token, restaurantId) {
        try {
            const response = await this.axiosInstance.post("/api/scheduling/agent/time-off/request/", { ...data, restaurant_id: restaurantId }, { headers: agentAuthHeaders(token) });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.response?.data?.error || error.message };
        }
    }
    async listTimeOffRequests(token, restaurantId, statusFilter = 'PENDING') {
        try {
            const response = await this.axiosInstance.get("/api/scheduling/agent/time-off/requests/", { params: { restaurant_id: restaurantId, status: statusFilter }, headers: agentAuthHeaders(token) });
            return response.data;
        }
        catch (error) {
            return { success: false, requests: [], error: error.response?.data?.error || error.message };
        }
    }
    async approveTimeOff(requestId, token, restaurantId) {
        try {
            const response = await this.axiosInstance.post("/api/scheduling/agent/time-off/approve/", { request_id: requestId, restaurant_id: restaurantId }, { headers: agentAuthHeaders(token) });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.response?.data?.error || error.message };
        }
    }
    async rejectTimeOff(requestId, token, reason, restaurantId) {
        try {
            const response = await this.axiosInstance.post("/api/scheduling/agent/time-off/reject/", { request_id: requestId, reason, restaurant_id: restaurantId }, { headers: agentAuthHeaders(token) });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.response?.data?.error || error.message };
        }
    }
    async listShiftSwaps(token, restaurantId) {
        try {
            const response = await this.axiosInstance.get("/api/scheduling/agent/shift-swaps/", { params: { restaurant_id: restaurantId }, headers: agentAuthHeaders(token) });
            return response.data;
        }
        catch (error) {
            return { success: false, swap_requests: [], error: error.response?.data?.error || error.message };
        }
    }
    async approveShiftSwap(swapRequestId, token, restaurantId) {
        try {
            const response = await this.axiosInstance.post("/api/scheduling/agent/shift-swaps/approve/", { swap_request_id: swapRequestId, restaurant_id: restaurantId }, { headers: agentAuthHeaders(token) });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.response?.data?.error || error.message };
        }
    }
    async rejectShiftSwap(swapRequestId, token, reason, restaurantId) {
        try {
            const response = await this.axiosInstance.post("/api/scheduling/agent/shift-swaps/reject/", { swap_request_id: swapRequestId, reason, restaurant_id: restaurantId }, { headers: agentAuthHeaders(token) });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.response?.data?.error || error.message };
        }
    }
    async listChecklistsForReview(token, restaurantId) {
        try {
            const response = await this.axiosInstance.get("/api/checklists/agent/review/list/", { params: { restaurant_id: restaurantId }, headers: agentAuthHeaders(token) });
            return response.data;
        }
        catch (error) {
            return { success: false, executions: [], error: error.response?.data?.error || error.message };
        }
    }
    async approveChecklist(executionId, token, restaurantId) {
        try {
            const response = await this.axiosInstance.post("/api/checklists/agent/review/approve/", { execution_id: executionId, restaurant_id: restaurantId }, { headers: agentAuthHeaders(token) });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.response?.data?.error || error.message };
        }
    }
    async rejectChecklist(executionId, token, reason, restaurantId) {
        try {
            const response = await this.axiosInstance.post("/api/checklists/agent/review/reject/", { execution_id: executionId, reason, restaurant_id: restaurantId }, { headers: agentAuthHeaders(token) });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.response?.data?.error || error.message };
        }
    }
    async listIncidents(token, restaurantId) {
        try {
            const response = await this.axiosInstance.get("/api/staff/agent/incidents/", { params: { restaurant_id: restaurantId }, headers: agentAuthHeaders(token) });
            return response.data;
        }
        catch (error) {
            return { success: false, incidents: [], error: error.response?.data?.error || error.message };
        }
    }
    async closeIncident(incidentId, token, resolutionNotes, restaurantId) {
        try {
            const response = await this.axiosInstance.post("/api/staff/agent/incidents/close/", { incident_id: incidentId, resolution_notes: resolutionNotes, restaurant_id: restaurantId }, { headers: agentAuthHeaders(token) });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.response?.data?.error || error.message };
        }
    }
    async escalateIncident(incidentId, token, restaurantId) {
        try {
            const response = await this.axiosInstance.post("/api/staff/agent/incidents/escalate/", { incident_id: incidentId, restaurant_id: restaurantId }, { headers: agentAuthHeaders(token) });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.response?.data?.error || error.message };
        }
    }
    async listFailedInvites(token, restaurantId) {
        try {
            const response = await this.axiosInstance.get("/api/agent/failed-invites/", { params: { restaurant_id: restaurantId }, headers: agentAuthHeaders(token) });
            return response.data;
        }
        catch (error) {
            return { success: false, failed_invites: [], error: error.response?.data?.error || error.message };
        }
    }
    async retryInvite(logId, token, restaurantId) {
        try {
            const response = await this.axiosInstance.post("/api/agent/retry-invite/", { log_id: logId, restaurant_id: restaurantId }, { headers: agentAuthHeaders(token) });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.response?.data?.error || error.message };
        }
    }
    async lookupInvitation(phone, token) {
        try {
            const response = await this.axiosInstance.get("/api/agent/lookup-invitation/", {
                headers: {
                    'Authorization': `Bearer ${token}`
                },
                params: { phone }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to lookup invitation:", error.message);
            if (error.response && error.response.data) {
                console.error(JSON.stringify(error.response.data));
                return { success: false, ...error.response.data };
            }
            return { success: false, error: error.message };
        }
    }
    async acceptInvitation(data, token) {
        try {
            const response = await this.axiosInstance.post("/api/agent/accept-invitation/", data, {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to accept invitation:", error.message);
            if (error.response && error.response.data) {
                console.error(JSON.stringify(error.response.data));
                return { success: false, ...error.response.data };
            }
            return { success: false, error: error.message };
        }
    }
    /** Single-step account activation by phone. Used by account_activation tool only. */
    async activateAccountByPhone(phone, _tokenLegacy) {
        try {
            const phoneDigits = normalizeActivationPhoneDigits(String(phone ?? "").replace(/\D/g, "").trim());
            if (!phoneDigits || phoneDigits.length < 6) {
                return {
                    success: false,
                    error: "Invalid phone",
                    message_for_user: "We couldn't read a valid phone number for activation. Please try again from WhatsApp with the same number your manager invited.",
                };
            }
            const auth = mizanLuaWebhookAuthorization();
            if (!auth?.Authorization) {
                return {
                    success: false,
                    error: "No agent key configured",
                    message_for_user: "Activation is temporarily unavailable. Please contact your manager — the assistant is not linked to the server.",
                };
            }
            const response = await this.axiosInstance.post("/api/agent/account-activation/", { phone: phoneDigits }, {
                headers: { Authorization: auth.Authorization },
                timeout: 30000,
                validateStatus: () => true,
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Account activation failed:", error.message);
            if (error.response?.status === 401) {
                return {
                    success: false,
                    error: "Unauthorized",
                    message_for_user: "We couldn't reach the activation service. Please ask your manager to confirm Miya uses the same LUA_WEBHOOK_API_KEY as the Mizan backend, or open Miya from your Mizan dashboard.",
                };
            }
            if (error.response?.data) {
                const d = error.response.data;
                // Prefer message_for_user (never mentions PIN); never expose raw backend PIN errors
                const msg = d.message_for_user || (d.error && !String(d.error).toLowerCase().includes('pin') ? d.error : null) || "Activation could not be completed. Please try again or contact your manager.";
                return { success: false, error: msg, message_for_user: d.message_for_user || msg };
            }
            return {
                success: false,
                error: error.message,
                message_for_user: "We couldn't reach the activation service. Please try again in a moment from WhatsApp, or open Miya from your Mizan dashboard.",
            };
        }
    }
    async createIncidentReportForAgent(data) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey) {
            console.error("[ApiService] No agent key configured");
            return { success: false, error: "No agent key configured" };
        }
        try {
            const response = await this.axiosInstance.post("/api/reporting/agent/create-incident/", data, {
                headers: {
                    'Authorization': `Bearer ${agentKey}`
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to create incident report:", error.message);
            const data = error.response?.data;
            const errMsg = (typeof data?.message_for_user === "string" && data.message_for_user) ||
                (typeof data?.error === "string" && data.error) ||
                (data?.detail && (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail))) ||
                error.message;
            return { success: false, error: errMsg };
        }
    }
    /** Staff-captured guest order → Today's Orders (Mizan dashboard). Auth: LUA_WEBHOOK_API_KEY. */
    async createStaffCapturedOrderForAgent(data) {
        const agentKey = env("LUA_WEBHOOK_API_KEY") || env("WEBHOOK_API_KEY") || env("MIZAN_SERVICE_TOKEN");
        if (!agentKey) {
            console.error("[ApiService] No agent key configured");
            return { success: false, error: "No agent key configured" };
        }
        const payload = {
            restaurant_id: data.restaurant_id,
            items_summary: data.items_summary,
            phone: data.phone || data.staff_phone,
            channel: data.channel || "VOICE",
        };
        if (data.user_id)
            payload.user_id = data.user_id;
        if (data.order_type)
            payload.order_type = data.order_type;
        if (data.customer_name)
            payload.customer_name = data.customer_name;
        if (data.customer_phone)
            payload.customer_phone = data.customer_phone;
        if (data.table_or_location)
            payload.table_or_location = data.table_or_location;
        if (data.dietary_notes)
            payload.dietary_notes = data.dietary_notes;
        if (data.special_instructions)
            payload.special_instructions = data.special_instructions;
        try {
            const response = await this.axiosInstance.post("/api/notifications/agent/staff-captured-order/", payload, {
                headers: {
                    Authorization: `Bearer ${agentKey}`,
                },
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] createStaffCapturedOrderForAgent failed:", error.message);
            const d = error.response?.data;
            const errMsg = (typeof d?.error === "string" && d.error) ||
                (typeof d?.detail === "string" && d.detail) ||
                error.message;
            return { success: false, error: errMsg };
        }
    }
    async getStaffByPhoneForAgent(phone) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey) {
            console.error("[ApiService] No agent key configured");
            return { success: false, error: "No agent key configured" };
        }
        const phoneDigits = normalizeActivationPhoneDigits(String(phone ?? "").replace(/\D/g, "").trim());
        if (!phoneDigits || phoneDigits.length < 6) {
            return { success: false, found: false, error: "Invalid phone number" };
        }
        try {
            const response = await this.axiosInstance.get("/api/scheduling/agent/staff-by-phone/", {
                params: { phone: phoneDigits },
                headers: {
                    'Authorization': `Bearer ${agentKey}`
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to lookup staff by phone:", error.message);
            if (error.response && error.response.data) {
                return error.response.data;
            }
            return { success: false, error: error.message };
        }
    }
    async getMyShiftsForAgent(params) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey) {
            return { success: false, error: "No agent key configured" };
        }
        try {
            const response = await this.axiosInstance.get("/api/scheduling/agent/my-shifts/", {
                params,
                headers: { 'Authorization': `Bearer ${agentKey}` }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to fetch my shifts for agent:", error.message);
            if (error.response?.data) {
                return { success: false, error: error.response.data.error || error.message };
            }
            return { success: false, error: error.message };
        }
    }
    // ── Calendar write (Google Calendar via tenant onboarding tokens) ──
    async createCalendarEvent(restaurantId, payload) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            throw new Error("No agent key configured");
        try {
            const response = await this.axiosInstance.post("/api/dashboard/agent/calendar-events/create/", { restaurant_id: restaurantId, ...payload }, {
                headers: agentKeyBearerHeadersWithRestaurant(agentKey, restaurantId),
            });
            return response.data;
        }
        catch (error) {
            // Bubble up the 412 calendar_not_connected payload so the tool
            // can show the connect CTA instead of a generic error.
            if (error?.response?.status === 412 && error?.response?.data) {
                return error.response.data;
            }
            console.error("[ApiService] createCalendarEvent failed:", error.message);
            return { success: false, error: error?.response?.data?.error || error.message };
        }
    }
    // Voice reply (TTS over WhatsApp)
    async sendVoiceReply(restaurantId, payload) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            throw new Error("No agent key configured");
        try {
            const body = {
                restaurant_id: restaurantId,
                phone: payload.phone,
                text: payload.text,
            };
            if (payload.caption)
                body.caption = payload.caption;
            if (payload.voice)
                body.voice = payload.voice;
            if (typeof payload.speed === "number")
                body.speed = payload.speed;
            if (typeof payload.voiceNote === "boolean")
                body.voice_note = payload.voiceNote;
            const response = await this.axiosInstance.post("/api/notifications/agent/voice-reply/", body, {
                headers: agentKeyBearerHeadersWithRestaurant(agentKey, restaurantId),
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] sendVoiceReply failed:", error.message);
            return { success: false, error: error?.response?.data?.error || error.message };
        }
    }
    // Photo router (vision-based classify-and-act)
    async parsePhoto(restaurantId, payload) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            throw new Error("No agent key configured");
        try {
            // Endpoint expects multipart/form-data with an `image` file.
            // Miya almost always has a remote URL (WhatsApp media) so we
            // fetch it ourselves and forward as a file. base64 fallback
            // keeps unit tests / non-WA callers working.
            let imageBytes = null;
            let contentType = payload.contentType || "image/jpeg";
            if (payload.imageUrl) {
                const resp = await this.axiosInstance.get(payload.imageUrl, {
                    responseType: "arraybuffer",
                    timeout: 20000,
                    headers: {},
                    transformRequest: [(d) => d],
                });
                const raw = resp.data;
                if (raw == null) {
                    return { success: false, error: "Image download returned an empty response" };
                }
                imageBytes = Buffer.from(raw);
                contentType = resp.headers?.["content-type"] || contentType;
            }
            else if (payload.imageBase64) {
                const b64 = String(payload.imageBase64).trim();
                if (!b64) {
                    return { success: false, error: "No image provided to parse_photo" };
                }
                imageBytes = Buffer.from(b64, "base64");
            }
            if (!imageBytes) {
                return { success: false, error: "No image provided to parse_photo" };
            }
            // Use native Node 18+ FormData/Blob - no extra dep needed.
            const form = new FormData();
            const blob = new Blob([imageBytes], { type: contentType });
            form.append("image", blob, "photo.jpg");
            form.append("restaurant_id", restaurantId);
            if (payload.note)
                form.append("note", payload.note);
            if (payload.autoCreate === false)
                form.append("auto_create", "false");
            const response = await this.axiosInstance.post("/api/dashboard/agent/parse-photo/", form, {
                headers: agentKeyBearerHeadersWithRestaurant(agentKey, restaurantId),
                maxContentLength: Infinity,
                maxBodyLength: Infinity,
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] parsePhoto failed:", error.message);
            return { success: false, error: error?.response?.data?.error || error.message };
        }
    }
    /**
     * Sibling of parsePhoto for non-image attachments (PDF / DOCX / XLSX / CSV / TXT).
     * Forwards the document to /api/dashboard/agent/parse-document/ which extracts
     * text and runs an LLM classifier. NEVER use this for images — call parsePhoto
     * for image/* uploads instead.
     */
    async parseDocument(restaurantId, payload) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            throw new Error("No agent key configured");
        try {
            let docBytes = null;
            let contentType = payload.contentType || "application/octet-stream";
            let fileName = payload.fileName || "document";
            if (payload.documentUrl) {
                const resp = await this.axiosInstance.get(payload.documentUrl, {
                    responseType: "arraybuffer",
                    timeout: 30000,
                    headers: {},
                    transformRequest: [(d) => d],
                });
                const raw = resp.data;
                if (raw == null) {
                    return { success: false, error: "Document download returned an empty response" };
                }
                docBytes = Buffer.from(raw);
                contentType = resp.headers?.["content-type"] || contentType;
                if (!payload.fileName) {
                    try {
                        const u = new URL(payload.documentUrl);
                        const last = u.pathname.split("/").filter(Boolean).pop();
                        if (last)
                            fileName = decodeURIComponent(last);
                    }
                    catch { /* ignore */ }
                }
            }
            else if (payload.documentBase64) {
                const b64 = String(payload.documentBase64).trim();
                if (!b64) {
                    return { success: false, error: "No document provided to parse_document" };
                }
                docBytes = Buffer.from(b64, "base64");
            }
            if (!docBytes) {
                return { success: false, error: "No document provided to parse_document" };
            }
            const form = new FormData();
            const blob = new Blob([docBytes], { type: contentType });
            form.append("document", blob, fileName);
            form.append("restaurant_id", restaurantId);
            if (payload.note)
                form.append("note", payload.note);
            if (payload.autoCreate === false)
                form.append("auto_create", "false");
            const response = await this.axiosInstance.post("/api/dashboard/agent/parse-document/", form, {
                headers: agentKeyBearerHeadersWithRestaurant(agentKey, restaurantId),
                maxContentLength: Infinity,
                maxBodyLength: Infinity,
                // Don't throw on 4xx — backend deliberately returns wrong_tool /
                // unsupported / empty envelopes there for the agent to read.
                validateStatus: () => true,
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] parseDocument failed:", error.message);
            return { success: false, error: error?.response?.data?.error || error.message };
        }
    }
    // ── Finance / Accounts Payable ───────────────────────────────────────
    async recordInvoice(restaurantId, payload) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            throw new Error("No agent key configured");
        try {
            const response = await this.axiosInstance.post("/api/finance/agent/invoices/record/", { restaurant_id: restaurantId, ...payload }, {
                headers: agentKeyBearerHeadersWithRestaurant(agentKey, restaurantId),
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] recordInvoice failed:", error.message);
            return { success: false, error: error?.response?.data?.error || error.message };
        }
    }
    async markInvoicePaid(restaurantId, payload) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            throw new Error("No agent key configured");
        try {
            const response = await this.axiosInstance.post("/api/finance/agent/invoices/mark-paid/", { restaurant_id: restaurantId, ...payload }, {
                headers: agentKeyBearerHeadersWithRestaurant(agentKey, restaurantId),
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] markInvoicePaid failed:", error.message);
            return { success: false, error: error?.response?.data?.error || error.message };
        }
    }
    async listInvoices(restaurantId, opts = {}) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            throw new Error("No agent key configured");
        const { user_id, phone, email, overdue, ...rest } = opts;
        try {
            const response = await this.axiosInstance.get("/api/finance/agent/invoices/list/", {
                headers: agentKeyBearerHeadersWithRestaurant(agentKey, restaurantId),
                params: {
                    restaurant_id: restaurantId,
                    ...rest,
                    overdue: overdue ? "true" : undefined,
                    user_id: user_id || undefined,
                    phone: phone || undefined,
                    email: email || undefined,
                },
            });
            return response.data;
        }
        catch (error) {
            const body = error.response?.data;
            console.error("[ApiService] listInvoices failed:", error.message);
            return {
                success: false,
                error: body?.error || error.message,
                message_for_user: body?.message_for_user,
            };
        }
    }
    // Multi-location intelligence: returns per-location open requests +
    // clock-in stats for the tenant. Backend handles location attribution
    // (CustomUser.primary_location with primary-branch fallback).
    async getCrossLocationReport(restaurantId, period = 'today') {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            throw new Error("No agent key configured");
        try {
            const response = await this.axiosInstance.get("/api/dashboard/agent/cross-location-report/", {
                headers: agentKeyBearerHeadersWithRestaurant(agentKey, restaurantId),
                params: { restaurant_id: restaurantId, period },
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] cross-location report failed:", error.message);
            return { success: false, error: error?.response?.data?.error || error.message };
        }
    }
    // POS Methods
    async getPosSalesSummary(restaurantId, date) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            throw new Error("No agent key configured");
        try {
            const response = await this.axiosInstance.get("/api/pos/agent/sales-summary/", {
                headers: { 'Authorization': `Bearer ${agentKey}` },
                params: { restaurant_id: restaurantId, date }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to fetch POS sales summary:", error.message);
            return { success: false, error: error.message };
        }
    }
    async getPosTopItems(restaurantId, days = 7, limit = 10) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            throw new Error("No agent key configured");
        try {
            const response = await this.axiosInstance.get("/api/pos/agent/top-items/", {
                headers: { 'Authorization': `Bearer ${agentKey}` },
                params: { restaurant_id: restaurantId, days, limit }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to fetch POS top items:", error.message);
            return { success: false, error: error.message };
        }
    }
    async getPosStatus(restaurantId) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            throw new Error("No agent key configured");
        try {
            const response = await this.axiosInstance.get("/api/pos/agent/status/", {
                headers: { 'Authorization': `Bearer ${agentKey}` },
                params: { restaurant_id: restaurantId }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to fetch POS status:", error.message);
            return { success: false, error: error.message };
        }
    }
    async getPosSalesAnalysis(restaurantId, days = 7) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            throw new Error("No agent key configured");
        try {
            const response = await this.axiosInstance.get("/api/pos/agent/sales-analysis/", {
                headers: { 'Authorization': `Bearer ${agentKey}` },
                params: { restaurant_id: restaurantId, days }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to fetch sales analysis:", error.message);
            return { success: false, error: error.message };
        }
    }
    async getPosPrepList(restaurantId, date) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            throw new Error("No agent key configured");
        try {
            const response = await this.axiosInstance.get("/api/pos/agent/prep-list/", {
                headers: { 'Authorization': `Bearer ${agentKey}` },
                params: { restaurant_id: restaurantId, date }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to fetch prep list:", error.message);
            return { success: false, error: error.message };
        }
    }
    async syncPosMenu(restaurantId) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            throw new Error("No agent key configured");
        try {
            const response = await this.axiosInstance.post("/api/pos/agent/sync/menu/", {
                restaurant_id: restaurantId
            }, {
                headers: { 'Authorization': `Bearer ${agentKey}` }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to sync POS menu:", error.message);
            return { success: false, error: error.message };
        }
    }
    async syncPosOrders(restaurantId, startDate, endDate) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            throw new Error("No agent key configured");
        try {
            const response = await this.axiosInstance.post("/api/pos/agent/sync/orders/", {
                restaurant_id: restaurantId,
                start_date: startDate,
                end_date: endDate,
            }, {
                headers: { 'Authorization': `Bearer ${agentKey}` }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to sync POS orders:", error.message);
            return { success: false, error: error.message };
        }
    }
    async createStaffRequestForAgent(data) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey) {
            console.error("[ApiService] No agent key configured");
            return { success: false, error: "No agent key configured" };
        }
        try {
            const response = await this.axiosInstance.post("/api/staff/agent/requests/ingest/", data, {
                headers: {
                    'Authorization': `Bearer ${agentKey}`
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to create staff request:", error.message);
            const data = error.response?.data;
            if (data) {
                console.error(JSON.stringify(data));
            }
            const errMsg = (typeof data?.error === "string" && data.error) ||
                (typeof data?.detail === "string" && data.detail) ||
                (data?.detail && typeof data.detail === "object"
                    ? JSON.stringify(data.detail)
                    : null) ||
                error.message;
            return { success: false, error: errMsg };
        }
    }
    /** List recent staff requests so Miya can read what's in the inbox. */
    async listStaffRequestsForAgent(restaurantId, options) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            return { success: false, error: "No agent key configured" };
        try {
            const response = await this.axiosInstance.get("/api/staff/agent/requests/", {
                headers: agentKeyBearerHeadersWithRestaurant(agentKey, restaurantId),
                params: { restaurant_id: restaurantId, ...(options || {}) },
            });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.response?.data?.error || error.message };
        }
    }
    /** Assign or reassign a staff request to a specific user. */
    async assignStaffRequestForAgent(restaurantId, requestId, target) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            return { success: false, error: "No agent key configured" };
        try {
            const response = await this.axiosInstance.post("/api/staff/agent/requests/assign/", {
                restaurant_id: restaurantId,
                request_id: requestId,
                ...target,
            }, {
                headers: agentKeyBearerHeadersWithRestaurant(agentKey, restaurantId),
            });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.response?.data?.error || error.message };
        }
    }
    async approveStaffRequestForAgent(restaurantId, requestId) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            return { success: false, error: "No agent key configured" };
        try {
            const response = await this.axiosInstance.post("/api/staff/agent/requests/approve/", {
                restaurant_id: restaurantId,
                request_id: requestId,
            }, {
                headers: agentKeyBearerHeadersWithRestaurant(agentKey, restaurantId),
            });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.response?.data?.error || error.message };
        }
    }
    async rejectStaffRequestForAgent(restaurantId, requestId, reason) {
        const agentKey = env('LUA_WEBHOOK_API_KEY') || env('WEBHOOK_API_KEY') || env('MIZAN_SERVICE_TOKEN');
        if (!agentKey)
            return { success: false, error: "No agent key configured" };
        try {
            const response = await this.axiosInstance.post("/api/staff/agent/requests/reject/", {
                restaurant_id: restaurantId,
                request_id: requestId,
                reason: reason || '',
            }, {
                headers: agentKeyBearerHeadersWithRestaurant(agentKey, restaurantId),
            });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.response?.data?.error || error.message };
        }
    }
    // ─── MOROCCO FEATURES ──────────────────────────────────────────────────────
    async reportWaste(restaurantId, data) {
        try {
            const response = await this.axiosInstance.post("/api/inventory/agent/waste/", { restaurant_id: restaurantId, ...data }, { headers: agentAuthHeaders() });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.response?.data?.error || error.message, message_for_user: error.response?.data?.message_for_user };
        }
    }
    async getWasteSummary(restaurantId, date) {
        try {
            const response = await this.axiosInstance.get("/api/inventory/agent/waste/summary/", { params: { restaurant_id: restaurantId, date }, headers: agentAuthHeaders() });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.message };
        }
    }
    async startInventoryCount(restaurantId, staffId, category) {
        try {
            const response = await this.axiosInstance.post("/api/inventory/agent/count/start/", { restaurant_id: restaurantId, staff_id: staffId, category }, { headers: agentAuthHeaders() });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.message, message_for_user: error.response?.data?.message_for_user };
        }
    }
    async countInventoryItem(sessionId, countedQuantity) {
        try {
            const response = await this.axiosInstance.post("/api/inventory/agent/count/item/", { session_id: sessionId, counted_quantity: countedQuantity }, { headers: agentAuthHeaders() });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.message, message_for_user: error.response?.data?.message_for_user };
        }
    }
    async sendSupplierOrder(restaurantId, data) {
        try {
            const response = await this.axiosInstance.post("/api/inventory/agent/supplier-order/", { restaurant_id: restaurantId, ...data }, { headers: agentAuthHeaders() });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.message, message_for_user: error.response?.data?.message_for_user };
        }
    }
    async openCashSession(restaurantId, staffId, openingFloat) {
        try {
            const response = await this.axiosInstance.post("/api/timeclock/agent/cash/open/", { restaurant_id: restaurantId, staff_id: staffId, opening_float: openingFloat }, { headers: agentAuthHeaders() });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.message, message_for_user: error.response?.data?.message_for_user };
        }
    }
    async closeCashSession(data) {
        try {
            const response = await this.axiosInstance.post("/api/timeclock/agent/cash/close/", data, { headers: agentAuthHeaders() });
            return response.data;
        }
        catch (error) {
            return { success: false, error: error.message, message_for_user: error.response?.data?.message_for_user };
        }
    }
    // ─── RESERVATIONS / APPOINTMENTS ────────────────────────────────────────
    async listReservationsForAgent(restaurantId, options) {
        try {
            const headers = agentAuthHeadersWithRestaurant(restaurantId, null);
            const response = await this.axiosInstance.get("/api/agent/reservations/", {
                headers,
                params: { restaurant_id: restaurantId, ...(options || {}) },
            });
            return response.data;
        }
        catch (error) {
            const err = error.response?.data?.error || error.message;
            return { success: false, error: err, reservations: [] };
        }
    }
    async createReservationForAgent(data) {
        try {
            const headers = agentAuthHeadersWithRestaurant(data.restaurant_id, null);
            const response = await this.axiosInstance.post("/api/agent/reservations/create/", data, { headers });
            return response.data;
        }
        catch (error) {
            const err = error.response?.data?.error || error.message;
            return { success: false, error: err };
        }
    }
    // ─── RECOGNITIONS / KUDOS ───────────────────────────────────────────────
    async recognizeStaffForAgent(data) {
        try {
            const headers = agentAuthHeadersWithRestaurant(data.restaurant_id, null);
            const response = await this.axiosInstance.post("/api/agent/recognize-staff/", data, { headers });
            return response.data;
        }
        catch (error) {
            const err = error.response?.data?.error || error.message;
            return { success: false, error: err };
        }
    }
    async listRecognitionsForAgent(restaurantId, options) {
        try {
            const headers = agentAuthHeadersWithRestaurant(restaurantId, null);
            const response = await this.axiosInstance.get("/api/agent/recognitions/", {
                headers,
                params: { restaurant_id: restaurantId, ...(options || {}) },
            });
            return response.data;
        }
        catch (error) {
            const err = error.response?.data?.error || error.message;
            return { success: false, error: err, recognitions: [] };
        }
    }
    // ─── HR LIFECYCLE ───────────────────────────────────────────────────────
    async hrLifecycleListForAgent(restaurantId, options) {
        try {
            const headers = agentAuthHeadersWithRestaurant(restaurantId, null);
            const response = await this.axiosInstance.get("/api/agent/hr-lifecycle/", {
                headers,
                params: { restaurant_id: restaurantId, ...(options || {}) },
            });
            return response.data;
        }
        catch (error) {
            const err = error.response?.data?.error || error.message;
            return { success: false, error: err, staff: [] };
        }
    }
    async hrLifecycleActionForAgent(data) {
        try {
            const headers = agentAuthHeadersWithRestaurant(data.restaurant_id, null);
            const response = await this.axiosInstance.post("/api/agent/hr-lifecycle/", data, { headers });
            return response.data;
        }
        catch (error) {
            const err = error.response?.data?.error || error.message;
            return { success: false, error: err };
        }
    }
    async grantRoleForAgent(data) {
        try {
            const headers = agentAuthHeadersWithRestaurant(data.restaurant_id, null);
            const response = await this.axiosInstance.post("/api/agent/grant-role/", data, { headers });
            return response.data;
        }
        catch (error) {
            const err = error.response?.data?.error || error.message;
            return { success: false, error: err };
        }
    }
    // ─── STAFF DOCUMENTS ────────────────────────────────────────────────────
    async listStaffDocumentsForAgent(restaurantId, options) {
        try {
            const headers = agentAuthHeadersWithRestaurant(restaurantId, null);
            const response = await this.axiosInstance.get("/api/agent/staff-documents/", {
                headers,
                params: { restaurant_id: restaurantId, ...(options || {}) },
            });
            return response.data;
        }
        catch (error) {
            const err = error.response?.data?.error || error.message;
            return { success: false, error: err, documents: [] };
        }
    }
    async createStaffDocumentForAgent(data) {
        try {
            const headers = agentAuthHeadersWithRestaurant(data.restaurant_id, null);
            const response = await this.axiosInstance.post("/api/agent/staff-documents/", data, { headers });
            return response.data;
        }
        catch (error) {
            const err = error.response?.data?.error || error.message;
            return { success: false, error: err };
        }
    }
    /**
     * Miya's "memory" — query the workspace activity log. Lets her answer
     * "who did X?", "what did Alice do today?", "who was task T assigned
     * to?" etc. Returns the raw event rows from the backend so callers can
     * summarise in natural language.
     */
    async getActivityLogForAgent(restaurantId, options, userToken) {
        try {
            const headers = agentAuthHeadersWithRestaurant(restaurantId, userToken);
            // Using a URLSearchParams so repeatable keys (``entity_type``,
            // ``action_type``) round-trip correctly — Django reads them
            // via ``request.query_params.getlist``.
            const params = new URLSearchParams();
            params.set("restaurant_id", restaurantId);
            if (options?.user_id)
                params.set("user_id", options.user_id);
            if (options?.target_user_id)
                params.set("target_user_id", options.target_user_id);
            if (options?.entity_id)
                params.set("entity_id", options.entity_id);
            if (options?.q)
                params.set("q", options.q);
            if (options?.since)
                params.set("since", options.since);
            if (options?.until)
                params.set("until", options.until);
            if (typeof options?.days === "number")
                params.set("days", String(options.days));
            if (typeof options?.limit === "number")
                params.set("limit", String(options.limit));
            (options?.entity_type || []).forEach((v) => params.append("entity_type", v));
            (options?.action_type || []).forEach((v) => params.append("action_type", v));
            const response = await this.axiosInstance.get(`/api/agent/activity-log/?${params.toString()}`, { headers });
            return response.data;
        }
        catch (error) {
            const err = error?.response?.data?.error || error.message;
            console.error("[ApiService] Failed to fetch activity log (agent):", err);
            return { success: false, error: err };
        }
    }
}
