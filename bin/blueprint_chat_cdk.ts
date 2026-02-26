#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import * as dotenv from "dotenv";
import { BlueprintChatCdkStack } from "../lib/stacks/blueprint_chat_cdk-stack";
import { EmailStack } from "../lib/stacks/email-stack";

dotenv.config();

const app = new cdk.App();
const env: cdk.Environment = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION,
};

new BlueprintChatCdkStack(app, "blueprint-chat-cdk-miguel", {
  description: "Blueprint Chat CDK Stack",
  env: env,
  NOTION_API_KEY: process.env.NOTION_API_KEY || "",
  DISCORD_API_KEY: process.env.DISCORD_API_KEY || "",
  DRIVE_API_KEY: process.env.DRIVE_API_KEY || "",
  WIKI_API_KEY: process.env.WIKI_API_KEY || "",
});

new EmailStack(app, "email-stack", {
  description: "Email Stack for Blueprint Chat",
  env: env,
  bucketName: `blueprint-emails-${env.account}-${env.region}`,
});

app.synth();
