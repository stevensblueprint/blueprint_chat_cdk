import {
  BedrockRuntimeClient,
  InvokeModelCommand,
  InvokeModelWithResponseStreamCommand,
} from "@aws-sdk/client-bedrock-runtime";

const REGION = process.env.REGION || "us-east-1";
const GLOBAL_MAX_TOKENS_PER_CALL = parseInt(
  process.env.GLOBAL_MAX_TOKENS_PER_CALL || "1024"
);

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "Content-Type,Authorization,x-api-key,Accept,Origin,X-Requested-With",
  "Access-Control-Allow-Methods": "POST,OPTIONS",
  "Content-Type": "application/json",
};

const client = new BedrockRuntimeClient({ region: REGION });

export const handler = async (event) => {
  try {
    if (event.httpMethod === "OPTIONS") {
      return { statusCode: 200, headers: CORS_HEADERS, body: "" };
    }

    const body = JSON.parse(event.body || "{}");
    const { modelId, messages, max_tokens, temperature } = body;

    if (!modelId || !messages) {
      return {
        statusCode: 400,
        headers: CORS_HEADERS,
        body: JSON.stringify({
          error: "Missing required fields: modelId, messages",
        }),
      };
    }

    const maxTokens = Math.min(max_tokens || 256, GLOBAL_MAX_TOKENS_PER_CALL);

    const payload = {
      anthropic_version: "bedrock-2023-05-31",
      messages: [{ role: "user", content: messages }],
      max_tokens: maxTokens,
      temperature: temperature || 0.5,
    };

    const command = new InvokeModelWithResponseStreamCommand({
      modelId,
      contentType: "application/json",
      accept: "application/json",
      body: JSON.stringify(payload),
    });

    const response = await client.send(command);

    let fullText = "";
    const usageTotals = {
      inputTokens: 0,
      outputTokens: 0,
      cacheReadTokens: 0,
      cacheWriteTokens: 0,
    };

    for await (const eventChunk of response.body) {
      console.log(eventChunk);
      if (eventChunk.chunk) {
        const rawBytes = Buffer.from(eventChunk.chunk.bytes);
        try {
          const parsed = JSON.parse(rawBytes.toString("utf-8"));
          if (parsed.text) fullText += parsed.text;
          if (parsed.metadata?.usage) {
            const usage = parsed.metadata.usage;
            usageTotals.inputTokens += usage.inputTokens || 0;
            usageTotals.outputTokens += usage.outputTokens || 0;
            usageTotals.cacheReadTokens += usage.cacheReadInputTokens || 0;
            usageTotals.cacheWriteTokens += usage.cacheWriteInputTokens || 0;
          }
        } catch {
          // ignore parse errors
        }
      }
    }

    return {
      statusCode: 200,
      headers: CORS_HEADERS,
      body: JSON.stringify({ completion: fullText, usage: usageTotals }),
    };
  } catch (err) {
    console.error(err);
    const statusCode =
      err.name === "AccessDeniedException"
        ? 403
        : err.name === "ValidationException"
          ? 400
          : 500;
    return {
      statusCode,
      headers: CORS_HEADERS,
      body: JSON.stringify({ error: err.message, details: err.stack }),
    };
  }
};
