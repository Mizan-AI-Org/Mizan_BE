import { LuaSkill } from "lua-cli";
import ScheduleOptimizerTool from "./tools/ScheduleOptimizerTool";
import StaffSchedulerTool from "./tools/StaffSchedulerTool";
import ChecklistStarterTool from "./tools/ChecklistStarterTool";
import ChecklistResponseTool from "./tools/ChecklistResponseTool";

export const staffOrchestratorSkill = new LuaSkill({
  name: "staff-orchestrator",
  description: "Staff scheduling, task orchestration, and checklist management",
  context: "Create tasks, optimize schedules, manage staff workflows, and conduct digital checklists via WhatsApp",
  tools: [
    new ScheduleOptimizerTool(),
    new StaffSchedulerTool(),
    new ChecklistStarterTool(),
    new ChecklistResponseTool(),
  ]
});