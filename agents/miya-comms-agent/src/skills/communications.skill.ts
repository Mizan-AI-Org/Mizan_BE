import { LuaSkill } from "lua-cli";
import StaffCommunicationTool from "./tools/StaffCommunicationTool";
import FormalAnnouncementTool from "./tools/FormalAnnouncementTool";
import { ListTemplatesTool, SendTemplateTool, GetTemplateTool } from "./tools/WhatsAppTemplateTool";
import WhatsAppFlowsTool from "./tools/WhatsAppFlowsTool";
import { VoiceReplyTool } from "./tools/VoiceReplyTool";

export const communicationsSkill = new LuaSkill({
  name: "communications",
  description:
    "Staff messaging (inform_staff), formal announcements (send_announcement), " +
    "WhatsApp template messages (list, get, send), WhatsApp Flows (interactive forms), " +
    "and voice replies (TTS over WhatsApp).",
  context:
    "This specialist handles all outbound communication: direct WhatsApp messages " +
    "to individual staff or groups (by name, role, tag, department), formal broadcast " +
    "announcements, pre-approved WhatsApp template messages for outbound beyond the " +
    "24-hour window, interactive WhatsApp Flows for structured data collection, and " +
    "voice replies (TTS audio messages over WhatsApp). " +
    "LEAVE REQUESTS: when staff want their own time off/leave/vacation, immediately send " +
    "whatsapp_flow(action='send', flow_key='leave_request') — never redirect them to a manager.",
  tools: [
    new StaffCommunicationTool(),
    new FormalAnnouncementTool(),
    new ListTemplatesTool(),
    new SendTemplateTool(),
    new GetTemplateTool(),
    new WhatsAppFlowsTool(),
    new VoiceReplyTool(),
  ],
});
