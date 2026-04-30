import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import {
  DynamoDBDocumentClient,
  PutCommand,
  QueryCommand,
  UpdateCommand,
  DeleteCommand,
} from "@aws-sdk/lib-dynamodb";
import {
  S3Client,
  PutObjectCommand,
  GetObjectCommand,
  DeleteObjectCommand,
} from "@aws-sdk/client-s3";
import { v4 as uuidv4 } from "uuid";

const dynamoClient = new DynamoDBClient({});
const docClient = DynamoDBDocumentClient.from(dynamoClient);
const s3Client = new S3Client({});

const TABLE_NAME = process.env.DYNAMODB_TABLE!;
const BUCKET_NAME = process.env.S3_BUCKET!;

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "Content-Type,Authorization",
  "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
  "Content-Type": "application/json",
};

export const handler = async (event: any) => {
  const { httpMethod, body, queryStringParameters } = event;

  if (httpMethod === "OPTIONS") {
    return { statusCode: 200, headers: CORS_HEADERS, body: "" };
  }

  const rawPath = event.path || "";
  const conversationIndex = rawPath.indexOf("/conversations");
  const path =
    conversationIndex !== -1 ? rawPath.substring(conversationIndex) : rawPath;

  const userId =
    queryStringParameters?.userId ||
    (body ? JSON.parse(body).userId : undefined);

  if (!userId || typeof userId !== "string") {
    return {
      statusCode: 400,
      headers: CORS_HEADERS,
      body: JSON.stringify({ error: "Missing or invalid userId format." }),
    };
  }

  if (!/^[a-zA-Z0-9-]+$/.test(userId) || userId.length > 50) {
    return {
      statusCode: 400,
      headers: CORS_HEADERS,
      body: JSON.stringify({ error: "Malformed userId" }),
    };
  }

  try {
    // GET /conversations
    if (httpMethod === "GET" && path === "/conversations") {
      try {
        const response = await docClient.send(
          new QueryCommand({
            TableName: TABLE_NAME,
            KeyConditionExpression: "userId = :uid",
            ExpressionAttributeValues: { ":uid": userId },
          }),
        );
        return {
          statusCode: 200,
          headers: CORS_HEADERS,
          body: JSON.stringify(response.Items),
        };
      } catch (error) {
        console.error("DynamoDB QueryCommand failed:", error);
        return {
          statusCode: 502,
          headers: CORS_HEADERS,
          body: JSON.stringify({
            error: "Failed to fetch conversations from database.",
          }),
        };
      }
    }

    // GET /conversations/{conversationId}
    if (httpMethod === "GET" && path.match(/^\/conversations\/[^\/]+$/)) {
      const conversationId = path.split("/")[2];
      const s3Key = `users/${userId}/conversations/${conversationId}/thread.json`;

      try {
        const s3Response = await s3Client.send(
          new GetObjectCommand({ Bucket: BUCKET_NAME, Key: s3Key }),
        );

        if (!s3Response.Body) {
          return {
            statusCode: 404,
            headers: CORS_HEADERS,
            body: JSON.stringify({ error: "Conversation data is empty." }),
          };
        }
        const threadData = await s3Response.Body.transformToString();
        return {
          statusCode: 200,
          headers: CORS_HEADERS,
          body: threadData,
        };
      } catch (error: any) {
        console.error("S3 GetObjectCommand failed:", error);
        if (error.name === "NoSuchKey") {
          return {
            statusCode: 404,
            headers: CORS_HEADERS,
            body: JSON.stringify({ error: "Conversation not found." }),
          };
        }
        return {
          statusCode: 502,
          headers: CORS_HEADERS,
          body: JSON.stringify({
            error: "Failed to retrieve conversation from storage.",
          }),
        };
      }
    }

    // POST /conversations
    if (httpMethod === "POST" && path === "/conversations") {
      if (!body) {
        return {
          statusCode: 400,
          headers: CORS_HEADERS,
          body: JSON.stringify({ error: "Missing request body" }),
        };
      }

      let parsedBody;
      try {
        parsedBody = JSON.parse(body);
      } catch (error) {
        return {
          statusCode: 400,
          headers: CORS_HEADERS,
          body: JSON.stringify({ error: "Invalid JSON in request body" }),
        };
      }

      if (!parsedBody.initialMessage) {
        return {
          statusCode: 400,
          headers: CORS_HEADERS,
          body: JSON.stringify({ error: "Missing initialMessage" }),
        };
      }

      const conversationId = parsedBody.conversationId || uuidv4();
      const title = parsedBody.title || "New Conversation";
      const timestamp = new Date().toISOString();
      const s3Key = `users/${userId}/conversations/${conversationId}/thread.json`;

      const initialThread = {
        conversationId,
        userId,
        title,
        createdAt: timestamp,
        updatedAt: timestamp,
        turns: [],
      };

      // Write to S3
      try {
        await s3Client.send(
          new PutObjectCommand({
            Bucket: BUCKET_NAME,
            Key: s3Key,
            Body: JSON.stringify(initialThread),
            ContentType: "application/json",
          }),
        );
      } catch (error) {
        console.error("S3 PutObjectCommand failed:", error);
        return {
          statusCode: 502,
          headers: CORS_HEADERS,
          body: JSON.stringify({
            error: "Failed to initialize conversation storage.",
          }),
        };
      }

      // Write to DynamoDB
      try {
        await docClient.send(
          new PutCommand({
            TableName: TABLE_NAME,
            Item: {
              userId,
              conversationId,
              title,
              createdAt: timestamp,
              updatedAt: timestamp,
              S3Key: s3Key,
            },
          }),
        );
      } catch (error) {
        console.error("DynamoDB PutCommand failed. Rolling back S3:", error);
        try {
          await s3Client.send(
            new DeleteObjectCommand({ Bucket: BUCKET_NAME, Key: s3Key }),
          );
        } catch (rollbackError) {
          console.error(
            "CRITICAL: Failed to rollback S3 object:",
            rollbackError,
          );
        }
        return {
          statusCode: 502,
          headers: CORS_HEADERS,
          body: JSON.stringify({
            error: "Failed to register conversation in database.",
          }),
        };
      }

      return {
        statusCode: 201,
        headers: CORS_HEADERS,
        body: JSON.stringify(initialThread),
      };
    }

    // POST /conversations/{conversationId}/turns
    if (
      httpMethod === "POST" &&
      path.match(/^\/conversations\/[^\/]+\/turns$/)
    ) {
      const conversationId = path.split("/")[2];

      let parsedBody;
      try {
        parsedBody = JSON.parse(body);
      } catch (error) {
        return {
          statusCode: 400,
          headers: CORS_HEADERS,
          body: JSON.stringify({ error: "Invalid JSON in request body" }),
        };
      }

      if (!parsedBody.message) {
        return {
          statusCode: 400,
          headers: CORS_HEADERS,
          body: JSON.stringify({ error: "Missing message in request body" }),
        };
      }

      const s3Key = `users/${userId}/conversations/${conversationId}/thread.json`;
      const newTimestamp = new Date().toISOString();

      let thread;
      try {
        const s3Object = await s3Client.send(
          new GetObjectCommand({ Bucket: BUCKET_NAME, Key: s3Key }),
        );
        if (!s3Object.Body) throw new Error("Empty body returned from S3");
        thread = JSON.parse(await s3Object.Body.transformToString());
      } catch (error: any) {
        console.error("S3 GetObjectCommand failed during append:", error);
        if (error.name === "NoSuchKey") {
          return {
            statusCode: 404,
            headers: CORS_HEADERS,
            body: JSON.stringify({ error: "Conversation not found." }),
          };
        }
        return {
          statusCode: 502,
          headers: CORS_HEADERS,
          body: JSON.stringify({
            error: "Failed to read conversation from storage.",
          }),
        };
      }

      const expectedUpdatedAt = thread.updatedAt;

      // Lock and Update DynamoDB
      try {
        await docClient.send(
          new UpdateCommand({
            TableName: TABLE_NAME,
            Key: { userId, conversationId },
            UpdateExpression: "set updatedAt = :newTime",
            ConditionExpression: "updatedAt = :expectedTime",
            ExpressionAttributeValues: {
              ":newTime": newTimestamp,
              ":expectedTime": expectedUpdatedAt,
            },
          }),
        );
      } catch (error: any) {
        if (error.name === "ConditionalCheckFailedException") {
          return {
            statusCode: 409,
            headers: CORS_HEADERS,
            body: JSON.stringify({
              error: "Concurrent modification detected. Please retry.",
            }),
          };
        }
        console.error("DynamoDB UpdateCommand failed:", error);
        return {
          statusCode: 502,
          headers: CORS_HEADERS,
          body: JSON.stringify({ error: "Failed to update database pointer." }),
        };
      }

      // Save back to S3
      const newTurn = {
        turnId: parsedBody.turnId || uuidv4(),
        createdAt: newTimestamp,
        messages: {
          user: { content: parsedBody.message },
          assistant: { content: parsedBody.assistantMessage || "" },
        },
      };
      thread.turns.push(newTurn);
      thread.updatedAt = newTimestamp;

      try {
        await s3Client.send(
          new PutObjectCommand({
            Bucket: BUCKET_NAME,
            Key: s3Key,
            Body: JSON.stringify(thread),
            ContentType: "application/json",
          }),
        );
      } catch (error) {
        console.error("S3 PutObjectCommand failed during append:", error);
        return {
          statusCode: 502,
          headers: CORS_HEADERS,
          body: JSON.stringify({
            error: "Database updated, but failed to write payload to storage.",
          }),
        };
      }

      return {
        statusCode: 200,
        headers: CORS_HEADERS,
        body: JSON.stringify(newTurn),
      };
    }

    // DELETE /conversations/{conversationId}
    if (httpMethod === "DELETE" && path.match(/^\/conversations\/[^\/]+$/)) {
      const conversationId = path.split("/")[2];
      const s3Key = `users/${userId}/conversations/${conversationId}/thread.json`;

      // Delete from S3
      try {
        await s3Client.send(
          new DeleteObjectCommand({ Bucket: BUCKET_NAME, Key: s3Key }),
        );
      } catch (error) {
        console.error("S3 DeleteObjectCommand failed:", error);
        return {
          statusCode: 502,
          headers: CORS_HEADERS,
          body: JSON.stringify({
            error: "Failed to delete conversation from storage.",
          }),
        };
      }

      // Delete from DynamoDB
      try {
        await docClient.send(
          new DeleteCommand({
            TableName: TABLE_NAME,
            Key: { userId, conversationId },
          }),
        );
      } catch (error) {
        console.error("DynamoDB DeleteCommand failed:", error);
        return {
          statusCode: 502,
          headers: CORS_HEADERS,
          body: JSON.stringify({
            error: "Storage deleted, but failed to remove database index.",
          }),
        };
      }

      return {
        statusCode: 200,
        headers: CORS_HEADERS,
        body: JSON.stringify({ status: "deleted", deletedId: conversationId }),
      };
    }

    return {
      statusCode: 404,
      headers: CORS_HEADERS,
      body: JSON.stringify({ error: "Endpoint Not Found" }),
    };
  } catch (error) {
    console.error("Critical unexpected error:", error);
    return {
      statusCode: 500,
      headers: CORS_HEADERS,
      body: JSON.stringify({ error: "Internal Server Error" }),
    };
  }
};