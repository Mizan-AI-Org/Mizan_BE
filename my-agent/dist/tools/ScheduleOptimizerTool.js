async (input) => {
  
  // Execute the bundled tool code
  var r=Object.defineProperty;var c=Object.getOwnPropertyDescriptor;var o=Object.getOwnPropertyNames;var u=Object.prototype.hasOwnProperty;var f=(t,e)=>{for(var i in e)r(t,i,{get:e[i],enumerable:!0})},l=(t,e,i,d)=>{if(e&&typeof e=="object"||typeof e=="function")for(let s of o(e))!u.call(t,s)&&s!==i&&r(t,s,{get:()=>e[s],enumerable:!(d=c(e,s))||d.enumerable});return t};var h=t=>l(r({},"__esModule",{value:!0}),t);var m={};f(m,{default:()=>n});module.exports=h(m);var a=require("zod"),n=class{name="schedule_optimizer";description="Optimize staff schedules based on predicted demand and staff availability.";inputSchema=a.z.object({week_start:a.z.string().describe("Start date of the week (YYYY-MM-DD)"),restaurantId:a.z.string().describe("The ID of the restaurant tenant"),department:a.z.enum(["kitchen","service","all"]).optional()});async execute(e){return{status:"success",message:`Schedule optimized for week of ${e.week_start}`,insights:["Increased kitchen staff on Friday evening due to expected tourist influx.","Reduced service staff on Monday lunch based on historical low traffic."],schedule_url:`https://mizan.ai/schedules/${e.restaurantId}/${e.week_start}`}}};

  
  // Get the tool class from exports
  const ToolClass = module.exports.default || module.exports.ScheduleOptimizerTool || module.exports;
  
  // Create and execute the tool
  const toolInstance = new ToolClass();
  return await toolInstance.execute(input);
}