import {
  BedrockRuntimeClient,
  ConverseStreamCommand,
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
};

const client = new BedrockRuntimeClient({ region: REGION });

const decodeChunkBytes = (chunkObj: any) => {
  if (!chunkObj.chunk?.bytes) return "";

  const bytesMap = chunkObj.chunk.bytes;
  const byteValues = Object.keys(bytesMap)
    .sort((a, b) => Number(a) - Number(b))
    .map((k) => bytesMap[k]);

  const uint8 = new Uint8Array(byteValues);
  const decoder = new TextDecoder();
  return decoder.decode(uint8);
};

exports.handler = awslambda.streamifyResponse(
  async (event, responseStream, _) => {
    try {
      const body = JSON.parse(event.body || "{}");
      const {
        modelId,
        messages,
        system,
        inferenceConfig,
        additionalModelRequestFields,
      } = body;

      const command = new ConverseStreamCommand({
        modelId,
        messages,
        system,
        inferenceConfig,
        additionalModelRequestFields,
      });

      const response = await client.send(command);

      const usageTotals = {
        inputTokens: 0,
        outputTokens: 0,
      };

      if (response.stream) {
        for await (const chunk of response.stream) {
          responseStream.write(JSON.stringify(chunk) + "\n");
        }
      }

      console.log("Usage totals:", usageTotals);
      responseStream.end();
    } catch (err) {
      console.error(err);
      responseStream.write("Internal Server Error\n");
      responseStream.write(err.message + "\n");
      responseStream.end();
    }
  }
);
