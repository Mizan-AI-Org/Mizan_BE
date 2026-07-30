import { LuaSkill } from "lua-cli";
import {
  ListInvoicesTool,
  RecordInvoiceTool,
  MarkInvoicePaidTool,
  MatchInvoicePoTool,
  ConfirmInvoicePoMatchTool,
  AttachInvoiceProofTool,
  ReturnInvoiceTool,
} from "./tools/InvoiceTools";
import PaymentApprovalTool from "./tools/PaymentApprovalTool";
import SalesReportTool from "./tools/SalesReportTool";
import SquarePosTool from "./tools/SquarePosTool";
import CashReconciliationTool from "./tools/CashReconciliationTool";
import SupplierOrderTool from "./tools/SupplierOrderTool";

export const financeSkill = new LuaSkill({
  name: "finance",
  description:
    "Invoice management (record, list, mark paid, proof of payment, return for changes), PayGuard payment approval ladders, " +
    "sales reporting and analysis, POS integration, cash drawer reconciliation, " +
    "and supplier order management.",
  context:
    "This specialist handles all financial operations: recording and tracking invoices " +
    "(with dedup on vendor+number+amount), listing invoices with filters (status, vendor, " +
    "due dates, overdue), PayGuard amount-tiered payment approvals (start/approve/reject/list), " +
    "marking invoices as paid only after approval when PayGuard is on, attaching proof of payment, " +
    "returning invoices for correction, sales reports, Square POS, cash drawer, and supplier POs. " +
    "For history questions (who paid, when paid, show proof), use list_invoices fields paid_by_name, paid_at, proof_of_payment_url.",
  tools: [
    new ListInvoicesTool(),
    new RecordInvoiceTool(),
    new MarkInvoicePaidTool(),
    new AttachInvoiceProofTool(),
    new ReturnInvoiceTool(),
    new MatchInvoicePoTool(),
    new ConfirmInvoicePoMatchTool(),
    new PaymentApprovalTool(),
    new SalesReportTool(),
    new SquarePosTool(),
    new CashReconciliationTool(),
    new SupplierOrderTool(),
  ],
});

