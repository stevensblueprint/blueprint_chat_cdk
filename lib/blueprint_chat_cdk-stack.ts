import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";
import * as path from "path";
import { Construct } from "constructs";

export class BlueprintChatCdkStack extends cdk.Stack {
  public readonly monthlyUsageTable: dynamodb.Table;
  public readonly transactionsTable: dynamodb.Table;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    this.monthlyUsageTable = new dynamodb.Table(
      this,
      "BedrockMonthlyUsageTable",
      {
        tableName: "Bedrock-Monthly-Usage",
        partitionKey: {
          name: "userArn",
          type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
          name: "month_year",
          type: dynamodb.AttributeType.STRING,
        },
        billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
        removalPolicy: cdk.RemovalPolicy.RETAIN,
        pointInTimeRecovery: true,
        encryption: dynamodb.TableEncryption.AWS_MANAGED,
      },
    );

    this.transactionsTable = new dynamodb.Table(
      this,
      "BedrockTransactionsTable",
      {
        tableName: "Bedrock-Transactions",
        partitionKey: {
          name: "userArn",
          type: dynamodb.AttributeType.STRING,
        },
        sortKey: {
          name: "timestamp",
          type: dynamodb.AttributeType.STRING,
        },
        billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
        removalPolicy: cdk.RemovalPolicy.RETAIN,
        pointInTimeRecovery: true,
        encryption: dynamodb.TableEncryption.AWS_MANAGED,
      },
    );

    this.transactionsTable.addGlobalSecondaryIndex({
      indexName: "month-year-index",
      partitionKey: {
        name: "month_year",
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: "timestamp",
        type: dynamodb.AttributeType.STRING,
      },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    this.transactionsTable.addGlobalSecondaryIndex({
      indexName: "model-id-index",
      partitionKey: {
        name: "model_id",
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: "timestamp",
        type: dynamodb.AttributeType.STRING,
      },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    const inferenceAuthorizerFn = new lambda.Function(
      this,
      "InferenceAuthorizerFn",
      {
        runtime: lambda.Runtime.PYTHON_3_10,
        handler: "main.handler",
        code: lambda.Code.fromAsset(
          path.join(
            __dirname,
            "..",
            "functions",
            "inference-authorizer-lambda",
          ),
        ),
        timeout: cdk.Duration.seconds(30),
        memorySize: 512,
        environment: {
          MONTHLY_USAGE_TABLE: this.monthlyUsageTable.tableName,
          TRANSACTIONS_TABLE: this.transactionsTable.tableName,
        },
      },
    );

    this.monthlyUsageTable.grantReadWriteData(inferenceAuthorizerFn);
    this.transactionsTable.grantReadWriteData(inferenceAuthorizerFn);

    const rule = new events.Rule(this, "BedrockInvokeRule", {
      description:
        "Triggers on Bedrock model invocations (CloudTrail management events).",
      eventPattern: {
        source: ["aws.bedrock"],
        detailType: ["AWS API Call via CloudTrail"],
        detail: {
          eventSource: ["bedrock.amazonaws.com"],
          eventName: ["InvokeModel", "InvokeModelWithResponseStream"],
        },
      },
    });

    rule.addTarget(new targets.LambdaFunction(inferenceAuthorizerFn));

    new cdk.CfnOutput(this, "MonthlyUsageTableName", {
      value: this.monthlyUsageTable.tableName,
      description: "Name of the Bedrock Monthly Usage table",
      exportName: "BedrockMonthlyUsageTableName",
    });

    new cdk.CfnOutput(this, "MonthlyUsageTableArn", {
      value: this.monthlyUsageTable.tableArn,
      description: "ARN of the Bedrock Monthly Usage table",
      exportName: "BedrockMonthlyUsageTableArn",
    });

    new cdk.CfnOutput(this, "TransactionsTableName", {
      value: this.transactionsTable.tableName,
      description: "Name of the Bedrock Transactions table",
      exportName: "BedrockTransactionsTableName",
    });

    new cdk.CfnOutput(this, "TransactionsTableArn", {
      value: this.transactionsTable.tableArn,
      description: "ARN of the Bedrock Transactions table",
      exportName: "BedrockTransactionsTableArn",
    });
  }
}
