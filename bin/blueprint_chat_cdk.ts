#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { BlueprintChatCdkStack } from "../lib/blueprint_chat_cdk-stack";

const app = new cdk.App();
new BlueprintChatCdkStack(app, "BlueprintChatCdkStack", {});
