import * as s3 from "aws-cdk-lib/aws-s3";
import * as sqs from "aws-cdk-lib/aws-sqs";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import LambdaLlmProxyConstruct from "../constructs/lambda_llm_proxy_construct";
import WebhookLambdaConstruct from "../constructs/webhook_lamda_construct";
import ChatHistoryConstruct from "../constructs/chat-history-construct";
import LambdaIngestionConstruct from "../constructs/lambda_ingestion_construct";

export interface BlueprintChatCdkStackProps extends cdk.StackProps {
  environment: string;
  NOTION_API_KEY: string;
  DISCORD_API_KEY: string;
  DRIVE_API_KEY: string;
  WIKI_API_KEY: string;
  WIKI_BASE_URL: string;
}
export class BlueprintChatCdkStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: BlueprintChatCdkStackProps) {
    super(scope, id, props);

    const envSuffix = props.environment === "" ? "" : `-${props.environment}`;

    const documentBucket = new s3.Bucket(this, "DocumentBucket", {
      bucketName: `blueprint-chat-documents-${cdk.Stack.of(this).account.toLowerCase()}${envSuffix}`,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const lambdaLlmProxy = new LambdaLlmProxyConstruct(this, "LambdaLlmProxy", {
      monthlyLimit: 6.6,
    });

    // Notion
    new WebhookLambdaConstruct(this, "NotionWebhookLambda", {
      codePath: "functions/webhook-listener-notion-lambda",
      description:
        "Lambda function to handle Notion webhooks and store documents in S3",
      documentBucket: documentBucket,
      environmentVariables: {
        NOTION_API_KEY: props.NOTION_API_KEY,
      },
    });

    // Discord
    new WebhookLambdaConstruct(this, "DiscordWebhookLambda", {
      codePath: "functions/webhook-listener-discord-lambda",
      description:
        "Lambda function to handle Discord webhooks and store documents in S3",
      documentBucket: documentBucket,
      environmentVariables: {
        DISCORD_API_KEY: props.DISCORD_API_KEY,
      },
    });

    new WebhookLambdaConstruct(this, "DriveWebhookLambda", {
      codePath: "functions/webhook-listener-drive-lambda",
      description:
        "Lambda function to handle Google Drive webhooks and store documents in S3",
      documentBucket: documentBucket,
      environmentVariables: {
        DRIVE_API_KEY: props.DRIVE_API_KEY,
      },
    });

    const chatHistoryConstruct = new ChatHistoryConstruct(
      this,
      "ChatHistoryConstruct",
      {
        environment: props.environment,
        s3BucketName: `blueprint-chat-history${envSuffix}`,
        chatHistoryTableName: `ChatHistory${envSuffix}`,
      },
    );

    // Wiki
    new WebhookLambdaConstruct(this, "WikiWebhookLambda", {
      codePath: "functions/webhook-listener-wiki-lambda",
      description:
        "Lambda function to handle Wiki webhooks and store documents in S3",
      documentBucket: documentBucket,
      environmentVariables: {
        WIKI_API_KEY: props.WIKI_API_KEY,
      },
    });

    const ingestionQueue = new sqs.Queue(this, "IngestionQueue", {
      queueName: `blueprint-chat-ingestion${envSuffix}`,
      visibilityTimeout: cdk.Duration.seconds(60),
      retentionPeriod: cdk.Duration.days(4),
    });

    new LambdaIngestionConstruct(this, "LambdaIngestionWorker", {
      ingestionQueue,
      documentBucket,
      notionApiKey: props.NOTION_API_KEY,
      driveApiKey: props.DRIVE_API_KEY,
      wikiApiKey: props.WIKI_API_KEY,
      wikiBaseUrl: props.WIKI_BASE_URL,
    });
  }
}
