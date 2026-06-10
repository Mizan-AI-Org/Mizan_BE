/**
 * WhatsApp Template Messages Tool
 *
 * Sends pre-approved WhatsApp template messages for outbound
 * notifications outside the 24-hour messaging window.
 * Uses Lua's Templates API for listing, inspecting, and sending.
 */
import { LuaTool, User, Templates } from "lua-cli";
import { z } from "zod";
import { noContextError, validationError, upstreamError } from "./_common/errors";

export class ListTemplatesTool implements LuaTool {
  name = "list_whatsapp_templates";
  description =
    "List available WhatsApp message templates. Use when the manager asks " +
    "'what templates do we have?', 'show me our WhatsApp templates', or " +
    "before sending a template to verify it exists and is approved.";

  inputSchema = z.object({
    channelId: z
      .string()
      .describe("WhatsApp channel ID from the agent's configuration."),
    search: z
      .string()
      .optional()
      .describe("Search query to filter templates by name (e.g. 'shift', 'welcome')."),
    page: z.number().optional().default(1).describe("Page number for pagination."),
  });

  async execute(input: z.infer<typeof this.inputSchema>) {
    const user = await User.get();
    if (!user) return noContextError();

    try {
      const result = await Templates.whatsapp.list(input.channelId, {
        search: input.search,
        page: input.page,
        limit: 10,
      });

      return {
        status: "success",
        templates: result.templates.map((t) => ({
          id: t.id,
          name: t.name,
          status: t.status,
          category: t.category,
          language: t.language,
        })),
        total: result.total,
        page: result.page,
        totalPages: result.totalPages,
        miya_directive:
          "List the templates for the manager. Only APPROVED templates can be sent. " +
          "Show name, category, and status. Offer to send any of them.",
      };
    } catch (error) {
      return upstreamError((error as Error).message);
    }
  }
}

export class SendTemplateTool implements LuaTool {
  name = "send_whatsapp_template";
  description =
    "Send a pre-approved WhatsApp template message to one or more recipients. " +
    "Use for: shift reminders, announcements, welcome messages, appointment confirmations, " +
    "or any outbound message OUTSIDE the 24-hour window. Templates must be APPROVED in " +
    "WhatsApp Manager. Use list_whatsapp_templates first to find the template ID.";

  inputSchema = z.object({
    channelId: z
      .string()
      .describe("WhatsApp channel ID."),
    templateId: z
      .string()
      .describe("Template ID (from list_whatsapp_templates or WhatsApp Manager)."),
    phoneNumbers: z
      .array(z.string())
      .describe(
        "Recipients in E.164 format (e.g. ['+212784476751']). Can send to multiple at once."
      ),
    values: z
      .object({
        header: z
          .record(z.string())
          .optional()
          .describe(
            "Header parameter values. For media: use image_url, video_url, or document_url + document_filename."
          ),
        body: z
          .record(z.string())
          .optional()
          .describe("Body parameter values matching template placeholders exactly."),
      })
      .optional()
      .describe("Template parameter values. Keys must match template placeholders exactly."),
  });

  async execute(input: z.infer<typeof this.inputSchema>) {
    const user = await User.get();
    if (!user) return noContextError();

    if (!input.phoneNumbers || input.phoneNumbers.length === 0) {
      return validationError("At least one phone number is required.");
    }

    try {
      const result = await Templates.whatsapp.send(
        input.channelId,
        input.templateId,
        {
          phoneNumbers: input.phoneNumbers,
          values: input.values,
        }
      );

      const sent = result.results.filter((r) => r.success).length;
      const failed = result.errors.length;

      return {
        status: "success",
        sent_count: sent,
        failed_count: failed,
        results: result.results.map((r) => ({
          phone: r.phoneNumber,
          success: r.success,
          messageId: r.messageId,
        })),
        errors: result.errors.map((e) => ({
          phone: e.phoneNumber,
          error: e.error,
        })),
        message: `Template sent to ${sent} recipient(s).${failed > 0 ? ` ${failed} failed.` : ""}`,
        miya_directive:
          "Confirm how many messages were sent successfully. If any failed, mention the specific " +
          "phone numbers and suggest checking them. Never expose raw error codes.",
      };
    } catch (error) {
      return upstreamError((error as Error).message);
    }
  }
}

export class GetTemplateTool implements LuaTool {
  name = "get_whatsapp_template";
  description =
    "Get details of a specific WhatsApp template including its components, " +
    "parameters, and status. Use to inspect what a template contains before sending.";

  inputSchema = z.object({
    channelId: z.string().describe("WhatsApp channel ID."),
    templateId: z.string().describe("Template ID to inspect."),
  });

  async execute(input: z.infer<typeof this.inputSchema>) {
    const user = await User.get();
    if (!user) return noContextError();

    try {
      const template = await Templates.whatsapp.get(
        input.channelId,
        input.templateId
      );

      return {
        status: "success",
        template: {
          id: template.id,
          name: template.name,
          status: template.status,
          category: template.category,
          language: template.language,
          components: template.components,
        },
        miya_directive:
          "Show the template details to the manager: name, status, language, and the message structure. " +
          "List the parameters that need to be filled when sending.",
      };
    } catch (error) {
      return upstreamError((error as Error).message);
    }
  }
}
