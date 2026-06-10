import { LuaSkill } from "lua-cli";
import IncidentReportTool from "./tools/IncidentReportTool";
import InventoryListTool from "./tools/InventoryListTool";
import InventoryCountTool from "./tools/InventoryCountTool";
import WasteReportTool from "./tools/WasteReportTool";
import { ParsePhotoTool } from "./tools/PhotoRouterTool";
import { ParseDocumentTool } from "./tools/DocumentRouterTool";

export const facilitiesSkill = new LuaSkill({
  name: "facilities",
  description:
    "Incident reporting, inventory management, waste tracking, " +
    "photo-to-action routing, and document parsing/extraction.",
  context:
    "Handles physical operations: safety incident reporting (text/voice/photo), " +
    "inventory listing, inventory counting sessions, waste reporting, photo-to-action " +
    "routing (invoice/schedule/equipment/incident classification), and document parsing " +
    "(PDF/DOCX/XLSX for invoice extraction).",
  tools: [
    new IncidentReportTool(),
    new InventoryListTool(),
    new InventoryCountTool(),
    new WasteReportTool(),
    new ParsePhotoTool(),
    new ParseDocumentTool(),
  ],
});
