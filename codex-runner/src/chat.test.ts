import assert from "node:assert/strict";
import { test } from "node:test";
import type { Thread, ThreadOptions, TurnOptions, ThreadEvent } from "@openai/codex-sdk";
import { runChat } from "./chat.js";

const input = {api_key:"test", base_url:"https://model.invalid/v1", model:"test", workspace:"/tmp",prompt:"Create beginner QA"};
const decision={reply:"开始收集资料",action:"build",request:"Python beginner QA",max_pages:5,base_run_id:""};
function fakeThread(events: ThreadEvent[]) {
  return {runStreamed:async (prompt: string, options: TurnOptions) => {
    assert.equal(prompt,input.prompt);assert.ok(options.outputSchema);assert.ok(options.signal);
    return {events:(async function*(){yield* events;})()};
  }} as unknown as Thread;
}
const events: ThreadEvent[] = [
  {type:"thread.started",thread_id:"saved-thread"},
  {type:"item.completed",item:{type:"agent_message",id:"msg",text:JSON.stringify(decision)}},
  {type:"turn.completed",usage:{input_tokens:120,cached_input_tokens:0,output_tokens:30,reasoning_output_tokens:0}},
];
test("new conversation streams thread ID, usage, and a typed decision",async()=>{
  const output:unknown[]=[];
  const codex={startThread:(options?:ThreadOptions)=>{assert.equal(options?.sandboxMode,"read-only");assert.equal(options?.approvalPolicy,"never");return fakeThread(events);},resumeThread:()=>{throw new Error("unexpected resume");}};
  await runChat(input,codex,e=>output.push(e));
  assert.deepEqual(output,[events[0],events[2],{type:"decision",decision}]);
});
test("follow-up resumes the same SDK thread",async()=>{
  const codex={startThread:()=>{throw new Error("unexpected new thread");},resumeThread:(id:string)=>{assert.equal(id,"saved-thread");return fakeThread(events);}};
  await runChat({...input,thread_id:"saved-thread"},codex,()=>{});
});
test("failed turn cannot dispatch a dataset action",async()=>{
  const codex={startThread:()=>fakeThread([{type:"turn.failed",error:{message:"provider error"}}]),resumeThread:()=>fakeThread([])};
  const output:unknown[]=[];
  await assert.rejects(()=>runChat(input,codex,e=>output.push(e)),/provider error/);
  assert.equal(output.length,0);
});
test("truncated stream is an error even if an agent message arrived",async()=>{
  const codex={startThread:()=>fakeThread(events.slice(0,2)),resumeThread:()=>fakeThread([])};
  await assert.rejects(()=>runChat(input,codex,()=>{}),/without completion/);
});
