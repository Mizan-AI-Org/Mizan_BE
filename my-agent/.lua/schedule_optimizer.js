async (input) => {
  
  // Execute the bundled tool code
  var n=Object.defineProperty;var m=Object.getOwnPropertyDescriptor;var o=Object.getOwnPropertyNames;var p=Object.prototype.hasOwnProperty;var h=(r,s)=>{for(var e in s)n(r,e,{get:s[e],enumerable:!0})},g=(r,s,e,i)=>{if(s&&typeof s=="object"||typeof s=="function")for(let a of o(s))!p.call(r,a)&&a!==e&&n(r,a,{get:()=>s[a],enumerable:!(i=m(s,a))||i.enumerable});return r};var c=r=>g(n({},"__esModule",{value:!0}),r);var k={};h(k,{default:()=>u});module.exports=c(k);var t=require("zod"),u=class{name="schedule_optimizer";description="Optimize staff schedules based on predicted demand and staff availability.";inputSchema=t.z.object({week_start:t.z.string().describe("Start date of the week (YYYY-MM-DD)"),department:t.z.enum(["kitchen","service","all"]).optional()});async execute(s,e){let i=e!=null&&e.get?e.get("restaurantId"):void 0,a=e!=null&&e.get?e.get("restaurantName"):"Unknown Restaurant";if(!i){let d=e?Object.keys(e):"null",f=e!=null&&e.user?"present":"missing",l=e!=null&&e.traits?"present":"missing";return{status:"error",message:`No restaurant context found. Debug: Keys=[${d}], User=${f}, Traits=${l}`}}return console.log(`[ScheduleOptimizerTool] Executing for ${a} (${i})`),{status:"success",restaurant:a,message:`Schedule optimized for week of ${s.week_start} for ${a}`,insights:["Increased kitchen staff on Friday evening due to expected tourist influx.","Reduced service staff on Monday lunch based on historical low traffic."],schedule_url:`https://mizan.ai/schedules/${i}/${s.week_start}`}}};

  
  // Get the tool class from exports
  const ToolClass = module.exports.default || module.exports.ScheduleOptimizerTool || module.exports;
  
  // Create and execute the tool
  const toolInstance = new ToolClass();
  return await toolInstance.execute(input);
}