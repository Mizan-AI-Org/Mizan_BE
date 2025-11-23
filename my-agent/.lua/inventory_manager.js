async (input) => {
  
  // Execute the bundled tool code
  var i=Object.defineProperty;var u=Object.getOwnPropertyDescriptor;var m=Object.getOwnPropertyNames;var g=Object.prototype.hasOwnProperty;var c=(s,e)=>{for(var t in e)i(s,t,{get:e[t],enumerable:!0})},l=(s,e,t,n)=>{if(e&&typeof e=="object"||typeof e=="function")for(let a of m(e))!g.call(s,a)&&a!==t&&i(s,a,{get:()=>e[a],enumerable:!(n=u(e,a))||n.enumerable});return s};var d=s=>l(i({},"__esModule",{value:!0}),s);var f={};c(f,{default:()=>o});module.exports=d(f);var r=require("zod"),o=class{name="inventory_manager";description="Manage restaurant inventory, check stock levels, and track waste.";inputSchema=r.z.object({action:r.z.enum(["check_stock","log_waste","get_alerts"]),item:r.z.string().optional(),quantity:r.z.number().optional(),unit:r.z.string().optional()});async execute(e,t){let n=t!=null&&t.get?t.get("restaurantId"):void 0,a=t!=null&&t.get?t.get("restaurantName"):"Unknown Restaurant";return n?(console.log(`[InventoryTool] Executing for ${a} (${n})`),e.action==="check_stock"?{status:"success",restaurant:a,item:e.item,current_stock:"15kg",status_level:"OK",message:`Stock for ${e.item} is sufficient at ${a}.`}:e.action==="log_waste"?{status:"success",restaurant:a,message:`Logged ${e.quantity}${e.unit} of ${e.item} as waste for ${a}.`,recommendation:"Consider reducing prep for this item by 10% next week."}:e.action==="get_alerts"?{restaurant:a,alerts:[{item:"Tomatoes",level:"CRITICAL",message:"Stock below 2kg. Reorder immediately."},{item:"Lamb",level:"LOW",message:"Stock below 5kg. Reorder suggested."}]}:{status:"error",message:"Invalid action"}):{status:"error",message:"No restaurant context found. Please ensure you are logged in."}}};

  
  // Get the tool class from exports
  const ToolClass = module.exports.default || module.exports.InventoryTool || module.exports;
  
  // Create and execute the tool
  const toolInstance = new ToolClass();
  return await toolInstance.execute(input);
}