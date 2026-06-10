/**
 * WhatsApp Flows Tool
 *
 * Sends structured WhatsApp Flows for form-based data collection.
 * Flows must be pre-built in WhatsApp Manager; this tool sends them
 * to the user via Lua's ::: flow formatting component.
 *
 * The agent returns the flow block in its response, and Lua renders
 * the CTA button. When the user submits the flow, Lua delivers the
 * submitted data back as a conversation message.
 */
import { LuaTool, User, Data } from "lua-cli";
import { z } from "zod";
import { noContextError, validationError } from "./_common/errors";

interface FlowTemplate {
  key: string;
  name: string;
  description: string;
  flow_id: string;
  flow_cta: string;
  body: string;
  header?: string;
  footer?: string;
  screen?: string;
}

const DEFAULT_FLOWS: FlowTemplate[] = [
  {
    key: "leave_request",
    name: "Leave / Time-Off Request",
    description: "Staff submits a structured leave/vacation request with dates, type, and reason.",
    flow_id: "LEAVE_REQUEST_FLOW_ID",
    flow_cta: "Request Leave",
    body: "Tap below to submit your leave request. Fill in the dates and reason.",
    header: "Leave Request",
    footer: "Your manager will be notified immediately",
  },
  {
    key: "incident_report",
    name: "Incident Report",
    description: "Staff reports a safety incident, equipment issue, or workplace event with structured fields.",
    flow_id: "INCIDENT_REPORT_FLOW_ID",
    flow_cta: "Report Incident",
    body: "Tap below to report an incident. Include as much detail as possible.",
    header: "Incident Report",
    footer: "Your report will be reviewed by management",
  },
  {
    key: "staff_onboarding",
    name: "Staff Onboarding",
    description: "New staff fills in personal details, emergency contacts, and documents during onboarding.",
    flow_id: "STAFF_ONBOARDING_FLOW_ID",
    flow_cta: "Start Onboarding",
    body: "Welcome! Tap below to complete your onboarding form.",
    header: "Welcome to the Team",
    footer: "This takes about 3 minutes",
  },
  {
    key: "shift_swap",
    name: "Shift Swap Request",
    description: "Staff requests to swap a shift with a colleague.",
    flow_id: "SHIFT_SWAP_FLOW_ID",
    flow_cta: "Request Swap",
    body: "Tap below to request a shift swap. Select the shift and preferred colleague.",
    header: "Shift Swap",
    footer: "Both you and your colleague will be notified",
  },
  {
    key: "feedback",
    name: "Staff Feedback",
    description: "Anonymous or named feedback from staff about workplace conditions, suggestions, or concerns.",
    flow_id: "STAFF_FEEDBACK_FLOW_ID",
    flow_cta: "Share Feedback",
    body: "Your voice matters! Tap below to share your feedback.",
    header: "Team Feedback",
    footer: "Responses can be anonymous",
  },
  {
    key: "daily_checkin",
    name: "Daily Check-In",
    description: "Quick daily status check-in for staff: how they feel, blockers, priorities.",
    flow_id: "DAILY_CHECKIN_FLOW_ID",
    flow_cta: "Check In",
    body: "Start your day right! Tap below for a quick check-in.",
    header: "Daily Check-In",
  },
  {
    key: "expense_claim",
    name: "Expense Claim",
    description: "Staff submits a reimbursement request with amount, category, and receipt.",
    flow_id: "EXPENSE_CLAIM_FLOW_ID",
    flow_cta: "Submit Expense",
    body: "Tap below to submit an expense claim. Attach your receipt photo.",
    header: "Expense Claim",
    footer: "Approval typically takes 1-2 business days",
  },
];

export default class WhatsAppFlowsTool implements LuaTool {
  name = "whatsapp_flow";
  description =
    "Send a structured WhatsApp Flow form to the user (leave requests, incident reports, " +
    "onboarding, shift swaps, feedback, expense claims). Also manages custom flow " +
    "configurations per restaurant. Use 'send' to present a flow form, 'list' to see " +
    "available flows, 'configure' to set up flow IDs for the restaurant.";

  inputSchema = z.object({
    action: z
      .enum(["send", "list", "configure"])
      .describe(
        "send: send a flow form to the current user. list: show available flows. configure: set a flow ID for a flow type."
      ),
    flow_key: z
      .string()
      .optional()
      .describe(
        "For 'send'/'configure': which flow template — leave_request, incident_report, staff_onboarding, shift_swap, feedback, daily_checkin, expense_claim, or a custom key."
      ),
    custom_body: z
      .string()
      .optional()
      .describe("For 'send': override the default body text with a custom message."),
    custom_cta: z
      .string()
      .optional()
      .describe("For 'send': override the CTA button text (max 30 chars, no emoji)."),
    screen: z
      .string()
      .optional()
      .describe("For 'send': specific screen ID to open first."),
    flow_id: z
      .string()
      .optional()
      .describe("For 'configure': the WhatsApp Flow ID from WhatsApp Manager."),
    restaurantId: z
      .string()
      .optional()
      .describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]."),
  });

  async execute(input: z.infer<typeof this.inputSchema>) {
    const user = await User.get();
    if (!user) return noContextError();

    const userData = (user as any).data || {};
    const profile = (user as any)._luaProfile || {};
    const restaurantId =
      input.restaurantId ||
      (user as any).restaurantId ||
      userData.restaurantId ||
      profile.restaurantId;

    const collection = restaurantId
      ? `flows-config-${restaurantId}`
      : "flows-config-default";

    switch (input.action) {
      case "list": {
        let configuredFlows: Record<string, string> = {};
        if (restaurantId) {
          try {
            const results = await Data.search(collection, "flow configuration", 20, 0.3);
            for (const r of results) {
              if (r.key && r.flow_id) {
                configuredFlows[r.key] = r.flow_id;
              }
            }
          } catch {
            // No configured flows yet
          }
        }

        const flows = DEFAULT_FLOWS.map((f) => ({
          key: f.key,
          name: f.name,
          description: f.description,
          configured: !!configuredFlows[f.key],
          flow_id: configuredFlows[f.key] || "Not configured",
        }));

        return {
          status: "success",
          flows,
          message: `${flows.length} flow templates available. ${Object.keys(configuredFlows).length} configured for this restaurant.`,
          miya_directive:
            "List the available flows for the manager. For unconfigured flows, mention they need a Flow ID from WhatsApp Manager. Offer to configure them.",
        };
      }

      case "configure": {
        if (!input.flow_key) {
          return validationError("Specify which flow to configure (e.g. 'leave_request').");
        }
        if (!input.flow_id) {
          return validationError(
            "Provide the WhatsApp Flow ID from WhatsApp Manager (Account tools > Flows)."
          );
        }
        if (!restaurantId) {
          return noContextError({ hint: "Restaurant ID needed to save flow configuration." });
        }

        await Data.create(
          collection,
          {
            key: input.flow_key,
            flow_id: input.flow_id,
            configuredAt: new Date().toISOString(),
            configuredBy: (user as any).uid || "manager",
          },
          `flow configuration ${input.flow_key} ${input.flow_id}`
        );

        return {
          status: "success",
          flow_key: input.flow_key,
          flow_id: input.flow_id,
          message: `Flow "${input.flow_key}" configured with ID ${input.flow_id}.`,
          miya_directive:
            "Confirm the flow has been configured. The manager can now use it by asking Miya to send this flow type to staff.",
        };
      }

      case "send": {
        if (!input.flow_key) {
          return validationError(
            "Specify which flow to send (e.g. 'leave_request', 'incident_report')."
          );
        }

        const template = DEFAULT_FLOWS.find((f) => f.key === input.flow_key);
        if (!template) {
          return {
            status: "error",
            code: "NOT_FOUND",
            message: `Unknown flow template "${input.flow_key}".`,
            available_keys: DEFAULT_FLOWS.map((f) => f.key),
            miya_directive:
              "Tell the user this flow type doesn't exist and list the available ones.",
          };
        }

        let actualFlowId = template.flow_id;
        if (restaurantId) {
          try {
            const results = await Data.search(
              collection,
              `flow configuration ${input.flow_key}`,
              1,
              0.7
            );
            if (results.length > 0 && results[0].flow_id) {
              actualFlowId = results[0].flow_id;
            }
          } catch {
            // Use default
          }
        }

        if (actualFlowId.endsWith("_FLOW_ID")) {
          return {
            status: "error",
            code: "NOT_CONFIGURED",
            message: `The "${template.name}" flow hasn't been configured yet.`,
            miya_directive:
              "Tell the manager this flow needs to be set up first. They need to: " +
              "1. Create the flow in WhatsApp Manager (Account tools > Flows). " +
              "2. Publish it. " +
              "3. Tell Miya the Flow ID so it can be configured. " +
              "Offer to configure it if they have the Flow ID.",
          };
        }

        const body = input.custom_body || template.body;
        const cta = input.custom_cta || template.flow_cta;

        const flowLines = ["::: flow", `flow_id=${actualFlowId}`, `flow_cta=${cta}`, `body=${body}`];
        if (template.header) flowLines.push(`header=${template.header}`);
        if (template.footer) flowLines.push(`footer=${template.footer}`);
        if (input.screen) flowLines.push(`screen=${input.screen}`);
        flowLines.push(":::");

        const flowBlock = flowLines.join("\n");

        return {
          status: "success",
          flow_key: input.flow_key,
          flow_name: template.name,
          formatted_flow: flowBlock,
          message: `Sending "${template.name}" flow to the user.`,
          miya_directive:
            "Include the formatted_flow block VERBATIM in your reply. " +
            "On WhatsApp it will render as a CTA button that opens the form. " +
            "Add a brief intro sentence in the user's language before the flow block. " +
            "When the user completes the form, you'll receive the submitted data as a message — " +
            "process it like any other user message (create the leave request, log the incident, etc.).",
        };
      }
    }
  }
}
