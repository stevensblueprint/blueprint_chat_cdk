import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import { Construct } from "constructs";
import LambdaLlmProxyConstruct from "./constructs/lambda_llm_proxy_construct";

export class BlueprintChatCdkStack extends cdk.Stack {
  public readonly monthlyUsageTable: dynamodb.ITable;
  public readonly transactionsTable: dynamodb.ITable;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const proxyConstruct = new LambdaLlmProxyConstruct(this, "LambdaLlmProxy", {
      monthlyLimit: 6.6,
    });

    this.monthlyUsageTable = proxyConstruct.monthlyUsageTable;
    this.transactionsTable = proxyConstruct.transactionsTable;
  }
}
