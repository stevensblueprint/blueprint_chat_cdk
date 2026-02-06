import * as s3 from "aws-cdk-lib/aws-s3";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import LambdaLlmProxyConstruct from "../constructs/lambda_llm_proxy_construct";
import WebhookLambdaConstruct from "../constructs/webhook_lamda_construct";

export interface BlueprintChatCdkStackProps extends cdk.StackProps {
  NOTION_API_KEY: string;
  DISCORD_API_KEY: string;
  DRIVE_API_KEY: string;
  WIKI_API_KEY: string;
}
export class BlueprintChatCdkStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: BlueprintChatCdkStackProps) {
    super(scope, id, props);

    const documentBucket = new s3.Bucket(this, "DocumentBucket", {
      bucketName: `${cdk.Stack.of(this).account.toLowerCase()}-blueprint-chat-documents`,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    new LambdaLlmProxyConstruct(this, "LambdaLlmProxy", {
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

    // Google Drive
    new WebhookLambdaConstruct(this, "DriveWebhookLambda", {
      codePath: "functions/webhook-listener-drive-lambda",
      description:
        "Lambda function to handle Google Drive webhooks and store documents in S3",
      documentBucket: documentBucket,
      environmentVariables: {
        DRIVE_API_KEY: props.DRIVE_API_KEY,
      },
    });

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
  }
}
