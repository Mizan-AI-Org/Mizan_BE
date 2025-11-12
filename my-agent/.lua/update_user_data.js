async (input) => {
  
  // Execute the bundled tool code
  var i=Object.defineProperty;var m=Object.getOwnPropertyDescriptor;var d=Object.getOwnPropertyNames;var g=Object.prototype.hasOwnProperty;var h=(a,e)=>{for(var t in e)i(a,t,{get:e[t],enumerable:!0})},f=(a,e,t,p)=>{if(e&&typeof e=="object"||typeof e=="function")for(let n of d(e))!g.call(a,n)&&n!==t&&i(a,n,{get:()=>e[n],enumerable:!(p=m(e,n))||p.enumerable});return a};var y=a=>f(i({},"__esModule",{value:!0}),a);var b={};h(b,{CreateInlineJobTool:()=>u,GetUserDataTool:()=>r,UpdateUserDataTool:()=>o,WritePoemTool:()=>c});module.exports=y(b);var s=require("zod"),r=class{name="get_user_data";description="Get the user data for a given user id";inputSchema=s.z.object({});constructor(){}async execute(e){let t=await User.get();return{success:!0,message:"User data retrieved",data:t.data,name:t.name,age:t.age}}},o=class{name="update_user_data";description="Update the user data for a given user id";inputSchema=s.z.object({data:s.z.object({name:s.z.string().optional(),age:s.z.number().optional()})});constructor(){}async execute(e){let t=await User.get();return t.name=e.data.name,t.age=e.data.age,await t.save(),{success:!0,message:"User data updated"}}},c=class{name="write_poem";description="Write a poem about a given topic";inputSchema=s.z.object({topic:s.z.string()});async execute(e){return await AI.generate("Write a poem about the following topic:",[{type:"text",text:e.topic}])}},u=class{name="create-job-to-notify-user";description="Create a new job to notify the user";inputSchema=s.z.object({message:s.z.string()});async execute(e){return{success:!0,job:await Jobs.create({name:"notify-user-new",description:"Notify the user",schedule:{type:"once",executeAt:new Date(Date.now()+0)},metadata:{message:e.message},execute:(async (job) => {
  var o=Object.defineProperty;var n=Object.getOwnPropertyDescriptor;var r=Object.getOwnPropertyNames;var u=Object.prototype.hasOwnProperty;var d=(t,e)=>{for(var a in e)o(t,a,{get:e[a],enumerable:!0})},m=(t,e,a,c)=>{if(e&&typeof e=="object"||typeof e=="function")for(let s of r(e))!u.call(t,s)&&s!==a&&o(t,s,{get:()=>e[s],enumerable:!(c=n(e,s))||c.enumerable});return t};var i=t=>m(o({},"__esModule",{value:!0}),t);var g={};d(g,{default:()=>f});module.exports=i(g);var x=async t=>{let e=await t.user(),a=t.metadata;return console.log("Metadata",a),await e.send([{type:"text",text:a.message}]),{success:!0,message:"User notified"}},f=x;

  const executeFunction = module.exports.default || module.exports;
  return await executeFunction(job);
})})}}};0&&(module.exports={CreateInlineJobTool,GetUserDataTool,UpdateUserDataTool,WritePoemTool});

  
  // Get the tool class from exports
  const ToolClass = module.exports.default || module.exports.UpdateUserDataTool || module.exports;
  
  // Create and execute the tool
  const toolInstance = new ToolClass();
  return await toolInstance.execute(input);
}