/** Structured conversational controller. Python owns all dataset side effects. */
import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { Codex } from "@openai/codex-sdk";

type ChatInput = {api_key: string; base_url: string; model: string; workspace: string; thread_id?: string; prompt: string};
const schema = {
  type: "object", additionalProperties: false,
  required: ["reply", "action", "request", "max_pages", "base_run_id", "target_rows", "stop_after"],
  properties: {
    reply: { type: "string" },
    action: { type: "string", enum: ["reply", "build", "revise", "extend"] },
    request: { type: "string" }, max_pages: { type: "integer", minimum: 1, maximum: 1000 },
    target_rows: { type: "integer", minimum: 0, maximum: 10000 },
    base_run_id: { type: "string" },
    stop_after: { type: "string", enum: ["raw", "corpus", "qa"] },
  },
};

export async function runChat(input: ChatInput, codex: Pick<Codex, "startThread" | "resumeThread">, emit: (event: unknown) => void) {
  const options = {model: input.model, workingDirectory: input.workspace,
    sandboxMode: "read-only" as const, approvalPolicy: "never" as const,
    skipGitRepoCheck: true, networkAccessEnabled: false, webSearchMode: "disabled" as const};
  const thread = input.thread_id ? codex.resumeThread(input.thread_id, options) : codex.startThread(options);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 120_000);
  try {
    const { events } = await thread.runStreamed(input.prompt, {outputSchema: schema, signal: controller.signal});
    let finalResponse = "";
    let completed = false;
    for await (const event of events) {
      if (event.type === "thread.started") emit(event);
      if (event.type === "item.completed" && event.item.type === "agent_message") finalResponse = event.item.text;
      if (event.type === "turn.completed") {
        emit(event);
        completed = true;
      }
      if (event.type === "turn.failed") throw new Error(event.error.message);
    }
    if (!completed) throw new Error("Agent turn ended without completion");
    emit({type: "decision", decision: JSON.parse(finalResponse)});
  } finally { clearTimeout(timer); }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const input: ChatInput = JSON.parse(fs.readFileSync(0, "utf8"));
  const emit = (event: unknown) => process.stdout.write(JSON.stringify(event) + "\n");
  const codex = new Codex({apiKey: input.api_key, baseUrl: input.base_url,
    config: {
      model_provider: "datavoyager",
      model_providers: {datavoyager: {name: "DataVoyager", base_url: input.base_url,
        env_key: "DATAVOYAGER_API_KEY", wire_api: "responses"}},
      features: {shell_tool: false}, web_search: "disabled",
    }});
  runChat(input, codex, emit).catch(error => {
    let message = String(error?.message ?? error);
    if (input.api_key) message = message.split(input.api_key).join("[redacted]");
    emit({type: "failure", message});
    process.exitCode = 1;
  });
}
