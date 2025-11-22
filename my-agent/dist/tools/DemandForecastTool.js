async (input) => {
  
  // Execute the bundled tool code
  var s=Object.defineProperty;var d=Object.getOwnPropertyDescriptor;var c=Object.getOwnPropertyNames;var f=Object.prototype.hasOwnProperty;var u=(t,e)=>{for(var r in e)s(t,r,{get:e[r],enumerable:!0})},l=(t,e,r,o)=>{if(e&&typeof e=="object"||typeof e=="function")for(let a of c(e))!f.call(t,a)&&a!==r&&s(t,a,{get:()=>e[a],enumerable:!(o=d(e,a))||o.enumerable});return t};var p=t=>l(s({},"__esModule",{value:!0}),t);var h={};u(h,{default:()=>i});module.exports=p(h);var n=require("zod"),i=class{name="demand_forecast";description="Predict sales and customer footfall based on historical data, events, and weather.";inputSchema=n.z.object({date:n.z.string().describe("Date to forecast (YYYY-MM-DD)"),restaurantId:n.z.string().describe("The ID of the restaurant tenant")});async execute(e){return{date:e.date,forecast:{expected_revenue:"15,000 MAD",expected_covers:120,peak_hours:["13:00-14:30","20:00-22:00"]},factors:["Local Holiday: Eid Al-Fitr (High demand expected)","Weather: Sunny, 28\xB0C (Terrace seating optimized)","Tourist Season: High (Marrakech influx)"],recommendations:["Prepare extra Tagine ingredients.","Ensure full staff for dinner service."]}}};

  
  // Get the tool class from exports
  const ToolClass = module.exports.default || module.exports.DemandForecastTool || module.exports;
  
  // Create and execute the tool
  const toolInstance = new ToolClass();
  return await toolInstance.execute(input);
}