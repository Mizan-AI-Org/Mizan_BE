import { LuaSkill } from "lua-cli";
import {
  ListInvoicesTool,
  RecordInvoiceTool,
  MarkInvoicePaidTool,
  MatchInvoicePoTool,
  ConfirmInvoicePoMatchTool,
} from "./tools/InvoiceTools";
import SalesReportTool from "./tools/SalesReportTool";
import SquarePosTool from "./tools/SquarePosTool";
import CashReconciliationTool from "./tools/CashReconciliationTool";
import SupplierOrderTool from "./tools/SupplierOrderTool";

export const financeSkill = new LuaSkill({
  name: "finance",
  description:
    "Invoice management (record, list, mark paid), sales reporting and analysis, " +
    "POS integration (Square, Custom API, Toast, Clover), cash drawer reconciliation, " +
    "and supplier order management.",
  context:
    "This specialist handles all financial operations: recording and tracking invoices " +
    "(with dedup on vendor+number+amount), listing invoices with filters (status, vendor, " +
    "due dates, overdue), marking invoices as paid, sales reports with top items, " +
    "Square POS integration (sales analysis, prep lists, menu sync, order sync), " +
    "cash drawer open/close reconciliation, and supplier purchase orders.",
  tools: [
    new ListInvoicesTool(),
    new RecordInvoiceTool(),
    new MarkInvoicePaidTool(),
    new MatchInvoicePoTool(),
    new ConfirmInvoicePoMatchTool(),
    new SalesReportTool(),
    new SquarePosTool(),
    new CashReconciliationTool(),
    new SupplierOrderTool(),
  ],
});
