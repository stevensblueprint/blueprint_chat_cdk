#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import * as dotenv from "dotenv";
import { BlueprintChatCdkStack } from "../lib/blueprint_chat_cdk-stack";

dotenv.config();

const app = new cdk.App();
new BlueprintChatCdkStack(app, "BlueprintChatCdkStack", {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
});
