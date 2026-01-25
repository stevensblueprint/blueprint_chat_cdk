import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import LambdaLlmProxyConstruct from "./constructs/lambda_llm_proxy_construct";

export class BlueprintChatCdkStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    new LambdaLlmProxyConstruct(this, "LambdaLlmProxy", {
      monthlyLimit: 6.6,
    });
  }
}
