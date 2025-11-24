async (input) => {
  
  // Execute the bundled tool code
  var i=Object.defineProperty;var o=Object.getOwnPropertyDescriptor;var u=Object.getOwnPropertyNames;var f=Object.prototype.hasOwnProperty;var c=(r,a)=>{for(var e in a)i(r,e,{get:a[e],enumerable:!0})},l=(r,a,e,s)=>{if(a&&typeof a=="object"||typeof a=="function")for(let t of u(a))!f.call(r,t)&&t!==e&&i(r,t,{get:()=>a[t],enumerable:!(s=o(a,t))||s.enumerable});return r};var m=r=>l(i({},"__esModule",{value:!0}),r);var g={};c(g,{default:()=>n});module.exports=m(g);var d=require("zod"),n=class{name="demand_forecast";description="Predict sales and customer footfall based on historical data, events, and weather.";inputSchema=d.z.object({date:d.z.string().describe("Date to forecast (YYYY-MM-DD)")});async execute(a,e){let s=e!=null&&e.get?e.get("restaurantId"):void 0,t=e!=null&&e.get?e.get("restaurantName"):"Unknown Restaurant";return s?(console.log(`[DemandForecastTool] Executing for ${t} (${s})`),{date:a.date,restaurant:t,forecast:{expected_revenue:"15,000 MAD",expected_covers:120,peak_hours:["13:00-14:30","20:00-22:00"]},factors:["Local Holiday: Eid Al-Fitr (High demand expected)","Weather: Sunny, 28\xB0C (Terrace seating optimized)","Tourist Season: High (Marrakech influx)"],recommendations:["Prepare extra Tagine ingredients.","Ensure full staff for dinner service."]}):{status:"error",message:"No restaurant context found. Please ensure you are logged in."}}};

  
  // Get the tool class from exports
  const ToolClass = module.exports.default || module.exports.DemandForecastTool || module.exports;
  
  // Create and execute the tool
  const toolInstance = new ToolClass();
  return await toolInstance.execute(input);
}