import { LuaSkill } from "lua-cli";
import ScheduleOptimizerTool from "./tools/ScheduleOptimizerTool";
import StaffSchedulerTool from "./tools/StaffSchedulerTool";
import ChecklistStarterTool from "./tools/ChecklistStarterTool";
import ChecklistResponseTool from "./tools/ChecklistResponseTool";
import IdentityResolutionTool from "./tools/IdentityResolutionTool";
import AcceptInvitationTool from "./tools/AcceptInvitationTool";

export const staffOrchestratorSkill = new LuaSkill({
  name: "staff-orchestrator",
  description: "Staff scheduling, task orchestration, checklist management, and identity resolution",
  context: "Create tasks, optimize schedules, manage staff workflows, conduct digital checklists, and resolve user identity",
  tools: [
    new ScheduleOptimizerTool(),
    new StaffSchedulerTool(),
    new ChecklistStarterTool(),
    new ChecklistResponseTool(),
    new IdentityResolutionTool(),
    new AcceptInvitationTool(),
  ]
});