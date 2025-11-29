import { LuaSkill } from "lua-cli";
import ScheduleOptimizerTool from "./tools/ScheduleOptimizerTool";
import StaffSchedulerTool from "./tools/StaffSchedulerTool";

export const staffOrchestratorSkill = new LuaSkill({
  name: "staff-orchestrator",
  description: "Staff scheduling and task orchestration",
  context: "Create tasks, optimize schedules, and manage staff workflows",
  tools: [
    new ScheduleOptimizerTool(),
    new StaffSchedulerTool(),
  ]
});