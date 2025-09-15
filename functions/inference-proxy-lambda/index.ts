import {
  BedrockRuntimeClient,
  ConverseStreamCommand,
} from "@aws-sdk/client-bedrock-runtime";

const REGION = process.env.REGION || "us-east-1";

const client = new BedrockRuntimeClient({ region: REGION });

exports.handler = awslambda.streamifyResponse(
  async (event, responseStream, _) => {
    console.log("Received event:", JSON.stringify(event, null, 2));

    const usage = {
      inputTokens: 0,
      outputTokens: 0,
    };

    try {
      const body = JSON.parse(event.body || "{}");
      console.log("Parsed request body: ", JSON.stringify(body, null, 2));

      const {
        modelId,
        messages,
        system,
        inferenceConfig,
        additionalModelRequestFields,
      } = body;

      if (!modelId || !messages) {
        const errorMsg = "Missing required parameters: modelId or messages";
        console.error(errorMsg);
        responseStream.write(JSON.stringify({ error: errorMsg }) + "\n");
        responseStream.end();
        return;
      }

      const command = new ConverseStreamCommand({
        modelId,
        messages,
        system,
        inferenceConfig,
        additionalModelRequestFields,
      });

      console.log(
        "Sending command to Bedrock: ",
        JSON.stringify(command, null, 2)
      );
      const response = await client.send(command);

      if (response.stream) {
        for await (const chunk of response.stream) {
          console.log("Received chunk: ", JSON.stringify(chunk, null, 2));
          responseStream.write(JSON.stringify(chunk) + "\n");
          if (chunk.metadata?.usage) {
            usage.inputTokens += chunk.metadata.usage.inputTokens || 0;
            usage.outputTokens += chunk.metadata.usage.outputTokens || 0;
          }
        }
      }

      console.log("Total token usage: ", usage);
      responseStream.end();
    } catch (err: any) {
      console.error("Error processing request:", err);

      responseStream.write(
        JSON.stringify({
          error: "Internal Server Error",
          message: err.message,
          stack: err.stack,
        }) + "\n"
      );
      responseStream.end();
    }
  }
);
