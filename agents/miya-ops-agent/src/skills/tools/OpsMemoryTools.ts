/**
 * Phase 2 ops tools: validate task, photo proof, search, check-in free-text, order station.
 */
import { LuaTool, User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

function getRestaurantId(user: any, inputRid?: string): string {
  const userData = user?.data || {};
  const profile = user?._luaProfile || {};
  const metadata =
    profile.metadata && typeof profile.metadata === "object" ? profile.metadata : {};
  return (
    inputRid ||
    user?.restaurantId ||
    userData.restaurantId ||
    profile.restaurantId ||
    metadata?.restaurantId ||
    metadata?.restaurant_id ||
    ""
  );
}

function getToken(user: any): string | null {
  const userData = user?.data || {};
  const profile = user?._luaProfile || {};
  const metadata =
    profile.metadata && typeof profile.metadata === "object" ? profile.metadata : {};
  return (
    user?.token ||
    userData.token ||
    profile.token ||
    metadata?.token ||
    metadata?.accessToken ||
    null
  );
}

function getPhone(user: any): string {
  const userData = user?.data || {};
  const profile = user?._luaProfile || {};
  const metadata =
    profile.metadata && typeof profile.metadata === "object" ? profile.metadata : {};
  return String(
    userData.phone || userData.phoneNumber || profile.phone || metadata?.phone || ""
  );
}

export class ValidateTaskTool implements LuaTool {
  name = "validate_task";
  description =
    "Manager validates any task (cross-cutting — Orders, Maintenance, Finance, etc.). " +
    "Clears the 'not validated by manager' label. Non-blocking — task was already visible.";

  inputSchema = z.object({
    task_id: z.string(),
    restaurantId: z.string().optional(),
  });

  constructor(private apiService: ApiService = new ApiService()) {}

  async execute(input: z.infer<typeof this.inputSchema>) {
    const user = await User.get();
    if (!user) return { status: "error", message: "No context." };
    const rid = getRestaurantId(user, input.restaurantId);
    const result = await this.apiService.validateDashboardTaskForAgent(
      rid,
      input.task_id,
      getToken(user)
    );
    return {
      status: result.success ? "success" : "error",
      message: result.message || result.error,
      record_id: result.task_id,
    };
  }
}

export class SubmitTaskProofTool implements LuaTool {
  name = "submit_task_proof";
  description =
    "Staff sends a photo via WhatsApp as proof of work or incident evidence for a task.";

  inputSchema = z.object({
    task_id: z.string(),
    media_url: z.string().describe("Public HTTPS URL of the photo"),
    restaurantId: z.string().optional(),
  });

  constructor(private apiService: ApiService = new ApiService()) {}

  async execute(input: z.infer<typeof this.inputSchema>) {
    const user = await User.get();
    if (!user) return { status: "error", message: "No context." };
    const rid = getRestaurantId(user, input.restaurantId);
    const result = await this.apiService.submitTaskProofForAgent(
      rid,
      { task_id: input.task_id, media_url: input.media_url },
      getToken(user)
    );
    return {
      status: result.success ? "success" : "error",
      message: result.message || result.error,
      record_id: result.task_id,
    };
  }
}

export class OpsSearchTool implements LuaTool {
  name = "ops_search";
  description =
    "Search any task or staff member and see what's assigned to them. " +
    "Also surfaces assignee_absent and manager validation labels.";

  inputSchema = z.object({
    q: z.string().min(2),
    restaurantId: z.string().optional(),
  });

  constructor(private apiService: ApiService = new ApiService()) {}

  async execute(input: z.infer<typeof this.inputSchema>) {
    const user = await User.get();
    if (!user) return { status: "error", message: "No context." };
    const rid = getRestaurantId(user, input.restaurantId);
    const result = await this.apiService.opsSearchForAgent(rid, input.q, getToken(user));
    return {
      status: result.success ? "success" : "error",
      ...result,
      message: result.success
        ? `Found ${(result.staff || []).length} staff, ${(result.tasks || []).length} tasks.`
        : result.error,
    };
  }
}

export class CheckinMessageTool implements LuaTool {
  name = "classify_checkin_message";
  description =
    "Classify free-text check-in messages like 'I'll be late' / 'stuck in traffic'. " +
    "Logs against the employee (no department routing). Use when staff send arrival notes without GPS clock-in.";

  inputSchema = z.object({
    text: z.string(),
    restaurantId: z.string().optional(),
  });

  constructor(private apiService: ApiService = new ApiService()) {}

  async execute(input: z.infer<typeof this.inputSchema>) {
    const user = await User.get();
    if (!user) return { status: "error", message: "No context." };
    const rid = getRestaurantId(user, input.restaurantId);
    const result = await this.apiService.classifyCheckinMessageForAgent(
      rid,
      { text: input.text, sender_phone: getPhone(user) },
      getToken(user)
    );
    return {
      status: result.success ? "success" : "error",
      classification: result.classification,
      record_id: result.note_id,
      task_id: result.task_id,
      message: result.message || result.error,
    };
  }
}

export class OrderStationTool implements LuaTool {
  name = "detect_order_station";
  description =
    "Detect Bar / Floor / Kitchen from sender role for orders. " +
    "Ask for clarification only if role is unclear.";

  inputSchema = z.object({
    role: z.string().optional(),
    restaurantId: z.string().optional(),
  });

  constructor(private apiService: ApiService = new ApiService()) {}

  async execute(input: z.infer<typeof this.inputSchema>) {
    const user = await User.get();
    if (!user) return { status: "error", message: "No context." };
    const rid = getRestaurantId(user, input.restaurantId);
    const result = await this.apiService.detectOrderStationForAgent(
      rid,
      { role: input.role },
      getToken(user)
    );
    return {
      status: "success",
      station: result.station,
      needs_clarification: result.needs_clarification,
      message: result.message,
    };
  }
}
