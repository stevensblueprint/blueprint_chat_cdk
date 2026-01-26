#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import * as dotenv from "dotenv";
import { BlueprintChatCdkStack } from "../lib/blueprint_chat_cdk-stack";

dotenv.config();

const app = new cdk.App();
new BlueprintChatCdkStack(app, "blueprint-chat-cdk", {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
  NOTION_API_KEY: process.env.NOTION_API_KEY || "",
  DISCORD_API_KEY: process.env.DISCORD_API_KEY || "",
  DRIVE_API_KEY: process.env.DRIVE_API_KEY || "",
  WIKI_API_KEY: process.env.WIKI_API_KEY || "",
});
