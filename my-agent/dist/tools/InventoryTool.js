async (input) => {
  
  // Execute the bundled tool code
  var i=Object.defineProperty;var c=Object.getOwnPropertyDescriptor;var m=Object.getOwnPropertyNames;var l=Object.prototype.hasOwnProperty;var g=(t,e)=>{for(var r in e)i(t,r,{get:e[r],enumerable:!0})},u=(t,e,r,n)=>{if(e&&typeof e=="object"||typeof e=="function")for(let a of m(e))!l.call(t,a)&&a!==r&&i(t,a,{get:()=>e[a],enumerable:!(n=c(e,a))||n.enumerable});return t};var k=t=>u(i({},"__esModule",{value:!0}),t);var d={};g(d,{default:()=>o});module.exports=k(d);var s=require("zod"),o=class{name="inventory_manager";description="Manage restaurant inventory, check stock levels, and track waste.";inputSchema=s.z.object({action:s.z.enum(["check_stock","log_waste","get_alerts"]),item:s.z.string().optional(),quantity:s.z.number().optional(),unit:s.z.string().optional(),restaurantId:s.z.string().describe("The ID of the restaurant tenant")});async execute(e){return e.action==="check_stock"?{status:"success",item:e.item,current_stock:"15kg",status_level:"OK",message:`Stock for ${e.item} is sufficient.`}:e.action==="log_waste"?{status:"success",message:`Logged ${e.quantity}${e.unit} of ${e.item} as waste.`,recommendation:"Consider reducing prep for this item by 10% next week."}:e.action==="get_alerts"?{alerts:[{item:"Tomatoes",level:"CRITICAL",message:"Stock below 2kg. Reorder immediately."},{item:"Lamb",level:"LOW",message:"Stock below 5kg. Reorder suggested."}]}:{status:"error",message:"Invalid action"}}};

  
  // Get the tool class from exports
  const ToolClass = module.exports.default || module.exports.InventoryTool || module.exports;
  
  // Create and execute the tool
  const toolInstance = new ToolClass();
  return await toolInstance.execute(input);
}