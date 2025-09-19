import {
  BedrockRuntimeClient,
  ConverseStreamCommand,
} from "@aws-sdk/client-bedrock-runtime";
import { STSClient, GetCallerIdentityCommand } from "@aws-sdk/client-sts";
import { DynamoDBClient, DescribeTableCommand } from "@aws-sdk/client-dynamodb";

const REGION = process.env.REGION || "us-east-1";

const bedrockClient = new BedrockRuntimeClient({ region: REGION });
const dynamodbClient = new DynamoDBClient({ region: "us-east-1" });

export async function verifyStsCredentials(
  accessKeyId: string,
  secretAccessKey: string,
  sessionToken: string
): Promise<string | null> {
  const client = new STSClient({
    region: "us-east-1",
    credentials: {
      accessKeyId,
      secretAccessKey,
      sessionToken,
    },
  });

  try {
    const command = new GetCallerIdentityCommand({});
    const response = await client.send(command);

    if (response.Arn) {
      const parts = response.Arn.split("/");
      return parts[parts.length - 1];
    }

    return null;
  } catch (error) {
    console.error("Invalid credentials: ", error);
    return null;
  }
}

exports.handler = awslambda.streamifyResponse(
  async (event, responseStream, _) => {
    console.log("Received event: ", JSON.stringify(event, null, 2));

    const usage = {
      inputTokens: 0,
      outputTokens: 0,
    };

    try {
      const body = JSON.parse(event.body || "{}");
      const headers = event.headers;
      const auth = {
        sessionToken: headers["x-aws-session-token"],
        accessKey: headers["x-aws-access-key"],
        secretKey: headers["x-aws-secret-key"],
      };
      console.log("Parsed request body: ", JSON.stringify(body, null, 2));

      const userArn = await verifyStsCredentials(
        auth.accessKey,
        auth.secretKey,
        auth.sessionToken
      );

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

      if (
        modelId != "anthropic.claude-3-haiku-20240307-v1:0" ||
        modelId != "anthropic.claude-3-5-haiku-20241022-v1:0" ||
        modelId != "anthropic.claude-sonnet-4-20250514-v1:0"
      ) {
        console.error("Invalid modelId: ", modelId);
        responseStream.write(
          JSON.stringify({ error: "Invalid model!" }) + "\n"
        );
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
      const response = await bedrockClient.send(command);

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
