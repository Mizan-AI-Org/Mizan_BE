import { LuaSkill } from "lua-cli";
import StaffClockInTool from "./tools/StaffClockInTool";
import StaffClockOutTool from "./tools/StaffClockOutTool";
import StaffSchedulerTool from "./tools/StaffSchedulerTool";
import ChecklistStarterTool from "./tools/ChecklistStarterTool";
import ChecklistRespondTool from "./tools/ChecklistRespondTool";
import StandaloneTasksTool from "./tools/StandaloneTasksTool";
import AttendanceTool from "./tools/AttendanceTool";
import ScheduleImportTool from "./tools/ScheduleImportTool";
import ScheduleOptimizerTool from "./tools/ScheduleOptimizerTool";
import OptimalStaffingTool from "./tools/OptimalStaffingTool";
import LaborReportExportTool from "./tools/LaborReportExportTool";
import StaffCapturedOrderTool from "./tools/StaffCapturedOrderTool";
import {
  ValidateTaskTool,
  SubmitTaskProofTool,
  OpsSearchTool,
  CheckinMessageTool,
  OrderStationTool,
} from "./tools/OpsMemoryTools";
import { ParseDocumentTool } from "./tools/DocumentRouterTool";
import ApiService from "../services/ApiService";

const apiService = new ApiService();

export const operationsSkill = new LuaSkill({
  name: "operations",
  description:
    "Scheduling, attendance management, clock-in/out, checklists, shift optimization, " +
    "labor reporting, staff task templates, ops memory (validate/proof/search/check-in notes), " +
    "and staff-captured guest orders. Covers all day-to-day operational actions.",
  context:
    "This specialist handles all operational actions: staff clock-in/out with geofencing, " +
    "shift creation/management (individual and team), shift swap approval, no-show marking, " +
    "coverage assignment, checklist start/respond flows, standalone task templates from shifts, " +
    "schedule import from photos/documents, schedule optimization, optimal staffing recommendations, " +
    "labor report exports, task validation/proof, ops search, check-in free-text classification, " +
    "order station detection, and capture_guest_order for Today's Orders. Always use staff_lookup " +
    "from the supervisor before scheduling if a staff name is involved. " +
    "ATTENDANCE: when staff say clock in/out, call staff_clock_in or staff_clock_out immediately — " +
    "relay the tool message field verbatim; location_required → \"Share your location to clock in.\"; " +
    "clocked_in → \"Clock-in recorded. Have a great shift {name}!\". " +
    "Late/absence free-text (I'll be late, malade, retard) → classify_checkin_message (preprocessor may already handle).",
  tools: [
    new StaffClockInTool(),
    new StaffClockOutTool(),
    new StaffSchedulerTool(),
    new ChecklistStarterTool(),
    new ChecklistRespondTool(),
    new StandaloneTasksTool(),
    new AttendanceTool(),
    new ScheduleImportTool(),
    new ParseDocumentTool(apiService),
    new ScheduleOptimizerTool(),
    new OptimalStaffingTool(),
    new LaborReportExportTool(),
    new StaffCapturedOrderTool(apiService),
    new ValidateTaskTool(apiService),
    new SubmitTaskProofTool(apiService),
    new OpsSearchTool(apiService),
    new CheckinMessageTool(apiService),
    new OrderStationTool(apiService),
  ],
});
