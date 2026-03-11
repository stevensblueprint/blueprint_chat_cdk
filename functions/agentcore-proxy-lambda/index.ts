import {
  BedrockAgentCoreClient,
  InvokeAgentRuntimeCommand,
} from "@aws-sdk/client-bedrock-agentcore";
import { Readable } from "stream";

const AGENT_RUNTIME_ARN = process.env.AGENT_RUNTIME_ARN!;
const AGENT_RUNTIME_ENDPOINT = process.env.AGENT_RUNTIME_ENDPOINT!;
const REGION = process.env.REGION || "us-east-1";

const client = new BedrockAgentCoreClient({ region: REGION });

declare const awslambda: {
  streamifyResponse: (
    handler: (event: any, responseStream: any, context: any) => Promise<void>,
  ) => any;
};

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "Content-Type,Authorization",
  "Access-Control-Allow-Methods": "POST,OPTIONS",
  "Content-Type": "application/json",
};

function getMethod(event: any): string {
  return (
    event?.requestContext?.http?.method ||
    event?.httpMethod ||
    "POST"
  ).toUpperCase();
}

function findStream(response: any): AsyncIterable<Uint8Array> | null {
  for (const key of Object.keys(response)) {
    if (key === "$metadata") continue;
    const val = response[key];
    if (!val) continue;
    if (
      typeof val[Symbol.asyncIterator] === "function" ||
      val instanceof Readable ||
      typeof val.pipe === "function"
    ) {
      console.log(
        `Found stream in field: "${key}" (constructor: ${val?.constructor?.name})`,
      );
      return val as AsyncIterable<Uint8Array>;
    }
  }
  console.warn("No stream field found. Response keys:", Object.keys(response));
  return null;
}

async function invokeAgent(event: any) {
  const body = JSON.parse(event.body || "{}");
  const { prompt, conversationId } = body;

  if (!prompt) {
    throw new Error("prompt is required");
  }

  const payload: Record<string, string> = { prompt };
  if (conversationId) payload.conversationId = conversationId;

  console.log(
    `Invoking agent runtime — ARN: ${AGENT_RUNTIME_ARN}, endpoint: ${AGENT_RUNTIME_ENDPOINT}, conversationId: ${conversationId}`,
  );

  const command = new InvokeAgentRuntimeCommand({
    agentRuntimeArn: AGENT_RUNTIME_ARN,
    qualifier: AGENT_RUNTIME_ENDPOINT,
    payload: JSON.stringify(payload),
  });

  const response = await client.send(command);
  console.log("Response keys:", Object.keys(response));
  return response;
}

// Streaming handler — invoked via Lambda Function URL with RESPONSE_STREAM
exports.streamingHandler = awslambda.streamifyResponse(
  async (event: any, responseStream: any, _context: any) => {
    if (getMethod(event) === "OPTIONS") {
      responseStream.end();
      return;
    }

    try {
      const response = await invokeAgent(event);
      const stream = findStream(response);

      if (stream) {
        for await (const chunk of stream) {
          responseStream.write(chunk);
        }
      }
    } catch (err: any) {
      console.error("Streaming error:", err);
      responseStream.write(
        JSON.stringify({ type: "error", error: err.message }),
      );
    }

    responseStream.end();
  },
);

// Buffered handler — invoked via API Gateway
exports.handler = async (event: any): Promise<any> => {
  if (getMethod(event) === "OPTIONS") {
    return { statusCode: 200, headers: CORS_HEADERS, body: "" };
  }

  try {
    const response = await invokeAgent(event);
    const stream = findStream(response);

    const chunks: Uint8Array[] = [];
    if (stream) {
      for await (const chunk of stream) {
        chunks.push(chunk);
      }
    }

    const raw = Buffer.concat(chunks).toString("utf-8");
    return {
      statusCode: 200,
      headers: CORS_HEADERS,
      body: raw,
    };
  } catch (err: any) {
    if (err.message === "prompt is required") {
      return {
        statusCode: 400,
        headers: CORS_HEADERS,
        body: JSON.stringify({ error: err.message }),
      };
    }
    console.error("Unhandled error:", err);
    return {
      statusCode: 500,
      headers: CORS_HEADERS,
      body: JSON.stringify({ error: err.message }),
    };
  }
};
