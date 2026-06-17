import { LuaSkill } from "lua-cli";
import HrLifecycleTool from "./tools/HrLifecycleTool";
import StaffDocumentTool from "./tools/StaffDocumentTool";
import StaffReportPdfTool from "./tools/StaffReportPdfTool";
import RecognitionTool from "./tools/RecognitionTool";
import RoleGrantTool from "./tools/RoleGrantTool";
import AccountActivationTool from "./tools/AccountActivationTool";
import StaffClockInTool from "./tools/StaffClockInTool";
import StaffClockOutTool from "./tools/StaffClockOutTool";
export const hrSkill = new LuaSkill({
    name: "human-resources",
    description: "HR lifecycle (roster, offboard, reactivate, transfer), staff documents " +
        "and licenses, PDF reports, recognition/kudos, role grants, account activation. " +
        "Also handles staff clock-in/out on WhatsApp when staff message this agent directly.",
    context: "Handles all HR operations: roster listing, offboarding, reactivation, role transfers, " +
        "staff document management (licenses, certificates with expiry tracking), staff PDF reports, " +
        "kudos/recognition awards, role grants/changes, and account activation by phone. " +
        "ACCOUNT ACTIVATION: when staff send the invite prefilled message ('Hi Mizan AI, I am ready to activate my account!' or similar), " +
        "call account_activation immediately — relay the tool message verbatim. No PIN. No app. " +
        "ATTENDANCE: when staff say clock in/out, call staff_clock_in or staff_clock_out immediately — " +
        "never refuse or redirect to another system. Relay the tool message verbatim.",
    tools: [
        new HrLifecycleTool(),
        new StaffDocumentTool(),
        new StaffReportPdfTool(),
        new RecognitionTool(),
        new RoleGrantTool(),
        new AccountActivationTool(),
        new StaffClockInTool(),
        new StaffClockOutTool(),
    ],
});
