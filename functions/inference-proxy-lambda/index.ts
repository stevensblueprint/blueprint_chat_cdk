import {
  BedrockRuntimeClient,
  ConverseStreamCommand,
} from "@aws-sdk/client-bedrock-runtime";
import { STSClient, GetCallerIdentityCommand } from "@aws-sdk/client-sts";
import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import {
  DynamoDBDocumentClient,
  GetCommand,
  PutCommand,
  UpdateCommand,
} from "@aws-sdk/lib-dynamodb";
import { CognitoJwtVerifier } from "aws-jwt-verify";

const REGION = process.env.REGION || "us-east-1";
const MONTHLY_USAGE_TABLE = process.env.MONTHLY_USAGE_TABLE;
const TRANSACTIONS_TABLE = process.env.TRANSACTIONS_TABLE;
const MONTHLY_LIMIT = process.env.MONTHLY_LIMIT;

const bedrockClient = new BedrockRuntimeClient({ region: REGION });
const dynamodbClient = new DynamoDBClient({ region: "us-east-1" });
const docClient = DynamoDBDocumentClient.from(dynamodbClient);

async function verifyStsCredentials(
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

async function verifyCognitoCredentials(
  accessToken: string
): Promise<string | null> {
  const verifier = CognitoJwtVerifier.create({
    userPoolId: "us-east-1_0QwnhHQ9T",
    tokenUse: null,
    clientId: "6bt3it6ivu28ng49ga4cvnkled",
  });

  try {
    const payload = await verifier.verify(accessToken);
    console.log("Token is valid. Payload: ", payload);
    return payload.username as string;
  } catch {
    console.log("Token not valid!");
    return null;
  }
}

function calculateCostFromTokens(
  inputTokens: number,
  outputTokens: number,
  modelId: string
): number {
  switch (modelId) {
    case "anthropic.claude-3-haiku-20240307-v1:0":
      return 0.00000025 * inputTokens + 0.00000125 * outputTokens;
    case "anthropic.claude-3-5-sonnet-20240620-v1:0":
      return 0.000003 * inputTokens + 0.000015 * outputTokens;
    default:
      return 0;
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
      const auth = headers["x-aws-session-token"]
        ? {
            sessionToken: headers["x-aws-session-token"],
            accessKey: headers["x-aws-access-key"],
            secretKey: headers["x-aws-secret-key"],
          }
        : {
            accessToken: headers["x-cognito-access-token"],
          };
      console.log("Parsed request body: ", JSON.stringify(body, null, 2));

      const userArn = headers["x-aws-session-token"]
        ? await verifyStsCredentials(
            auth.accessKey,
            auth.secretKey,
            auth.sessionToken
          )
        : await verifyCognitoCredentials(auth.accessToken);

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
        modelId != "anthropic.claude-3-haiku-20240307-v1:0" &&
        modelId != "anthropic.claude-3-5-sonnet-20240620-v1:0"
      ) {
        console.error("Invalid modelId: ", modelId);
        responseStream.write(
          JSON.stringify({ error: "Invalid model!" }) + "\n"
        );
        responseStream.end();
        return;
      }

      const now = new Date();
      const monthYear = `${(now.getMonth() + 1).toString().padStart(2, "0")}_${now.getFullYear()}`;
      const timestamp = now.toISOString().split(".")[0];

      const current_monthly_usage = (
        await docClient.send(
          new GetCommand({
            TableName: MONTHLY_USAGE_TABLE,
            Key: {
              userArn,
              month_year: monthYear,
            },
          })
        )
      ).Item;

      if (
        current_monthly_usage &&
        current_monthly_usage.cost >= MONTHLY_LIMIT!
      ) {
        const errorMsg =
          "Unable to process request. Current monthly usage exceeds the monthly limit.";
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

      const cost = calculateCostFromTokens(
        usage.inputTokens,
        usage.outputTokens,
        modelId
      );

      await docClient.send(
        new PutCommand({
          TableName: TRANSACTIONS_TABLE,
          Item: {
            userArn,
            timestamp,
            modelId,
            usage,
            cost,
          },
        })
      );

      await docClient.send(
        new UpdateCommand({
          TableName: MONTHLY_USAGE_TABLE,
          Key: { userArn, month_year: monthYear },
          UpdateExpression: "ADD invocations :one, cost :cost",
          ExpressionAttributeValues: {
            ":one": 1,
            ":cost": cost,
          },
        })
      );
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
