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

export const handler = async (event: any) => {
  const { httpMethod, path, body, queryStringParameters } = event;
  const userId =
    queryStringParameters?.userId || JSON.parse(body || "{}").userId;

  if (!userId) {
    return { statusCode: 400, body: "Missing userId" };
  }

  try {
    // GET /conversations?userId=...
    // - List conversations for a given userId
    if (httpMethod === "GET" && path === "/conversations") {
      const response = await docClient.send(
        new QueryCommand({
          TableName: TABLE_NAME,
          KeyConditionExpression: "userId = :uid",
          ExpressionAttributeValues: { ":uid": userId },
        }),
      );
      return { statusCode: 200, body: JSON.stringify(response.Items) };
    }

    // GET /conversations/conversationId?userId=...
    // - Get a specific conversation for a given userId and conversationId
    if (httpMethod === "GET" && path.match(/^\/conversations\/[^\/]+$/)) {
      const conversationId = path.split("/")[2];
      const s3Key = `users/${userId}/conversations/${conversationId}/thread.json`;

      const s3Response = await s3Client.send(
        new GetObjectCommand({
          Bucket: BUCKET_NAME,
          Key: s3Key,
        }),
      );
      const threadData = await s3Response.Body?.transformToString();
      return { statusCode: 200, body: threadData };
    }

    // POST /conversations
    // - Creates a new conversation
    if (httpMethod === "POST" && path === "/conversations") {
      const { initialMessage } = JSON.parse(body);
      const conversationId = uuidv4();
      const timestamp = new Date().toISOString();
      const s3Key = `users/${userId}/conversations/${conversationId}/thread.json`;

      // Write pointer to DynamoDB
      await docClient.send(
        new PutCommand({
          TableName: TABLE_NAME,
          Item: {
            userId,
            conversationId,
            title: "New Conversation",
            createdAt: timestamp,
            updatedAt: timestamp,
            S3Key: s3Key,
          },
        }),
      );

      const initialThread = {
        conversationId,
        userId,
        title: "New Conversation",
        createdAt: timestamp,
        updatedAt: timestamp,
        turns: [
          {
            turnId: uuidv4(),
            createdAt: timestamp,
            messages: { user: { content: initialMessage } },
          },
        ],
      };

      // Write full payload to S3
      await s3Client.send(
        new PutObjectCommand({
          Bucket: BUCKET_NAME,
          Key: s3Key,
          Body: JSON.stringify(initialThread),
          ContentType: "application/json",
        }),
      );

      return { statusCode: 201, body: JSON.stringify(initialThread) };
    }

    // POST /conversations/conversationId
    // - Append a turn to the specific conversation for a given conversationId
    if (
      httpMethod === "POST" &&
      path.match(/^\/conversations\/[^\/]+\/turns$/)
    ) {
      const conversationId = path.split("/")[2];
      const { message, assistantMessage } = JSON.parse(body);
      const s3Key = `users/${userId}/conversations/${conversationId}/thread.json`;
      const timestamp = new Date().toISOString();

      // Fetch existing thread from S3
      const s3Object = await s3Client.send(
        new GetObjectCommand({ Bucket: BUCKET_NAME, Key: s3Key }),
      );
      const thread = JSON.parse(await s3Object.Body!.transformToString());

      const newTurn = {
        turnId: uuidv4(),
        createdAt: timestamp,
        messages: {
          user: { content: message },
          assistant: { content: assistantMessage || "" },
        },
      };
      thread.turns.push(newTurn);
      thread.updatedAt = timestamp;

      // Save updated thread back to S3
      await s3Client.send(
        new PutObjectCommand({
          Bucket: BUCKET_NAME,
          Key: s3Key,
          Body: JSON.stringify(thread),
          ContentType: "application/json",
        }),
      );

      // Update DynamoDB updatedAt pointer
      await docClient.send(
        new UpdateCommand({
          TableName: TABLE_NAME,
          Key: { userId, conversationId },
          UpdateExpression: "set updatedAt = :u",
          ExpressionAttributeValues: { ":u": timestamp },
        }),
      );

      return { statusCode: 200, body: JSON.stringify(newTurn) };
    }

    // DELETE /conversations/conversationId
    // - Deletes the conversation for a given conversationId
    if (httpMethod === "DELETE" && path.match(/^\/conversations\/[^\/]+$/)) {
      const conversationId = path.split("/")[2];
      const s3Key = `users/${userId}/conversations/${conversationId}/thread.json`;

      await s3Client.send(
        new DeleteObjectCommand({
          Bucket: BUCKET_NAME,
          Key: s3Key,
        }),
      );

      await docClient.send(
        new DeleteCommand({
          TableName: TABLE_NAME,
          Key: { userId, conversationId },
        }),
      );

      return {
        statusCode: 200,
        body: JSON.stringify({ status: "deleted", deletedId: conversationId }),
      };
    }

    return { statusCode: 404, body: "Not Found" };
  } catch (error) {
    console.error(error);
    return {
      statusCode: 500,
      body: JSON.stringify({ error: "Internal Server Error" }),
    };
  }
};
