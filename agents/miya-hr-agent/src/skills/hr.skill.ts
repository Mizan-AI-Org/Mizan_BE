import { LuaSkill } from "lua-cli";
import HrLifecycleTool from "./tools/HrLifecycleTool";
import StaffDocumentTool from "./tools/StaffDocumentTool";
import StaffReportPdfTool from "./tools/StaffReportPdfTool";
import RecognitionTool from "./tools/RecognitionTool";
import RoleGrantTool from "./tools/RoleGrantTool";
import AccountActivationTool from "./tools/AccountActivationTool";

export const hrSkill = new LuaSkill({
  name: "human-resources",
  description:
    "HR lifecycle (roster, offboard, reactivate, transfer), staff documents " +
    "and licenses, PDF reports, recognition/kudos, role grants, account activation.",
  context:
    "Handles all HR operations: roster listing, offboarding, reactivation, role transfers, " +
    "staff document management (licenses, certificates with expiry tracking), staff PDF reports, " +
    "kudos/recognition awards, role grants/changes, and account activation by phone.",
  tools: [
    new HrLifecycleTool(),
    new StaffDocumentTool(),
    new StaffReportPdfTool(),
    new RecognitionTool(),
    new RoleGrantTool(),
    new AccountActivationTool(),
  ],
});
