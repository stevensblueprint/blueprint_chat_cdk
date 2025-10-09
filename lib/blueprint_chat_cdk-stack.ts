import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as path from "path";
import { Construct } from "constructs";
import * as apigw from "aws-cdk-lib/aws-apigateway";

export class BlueprintChatCdkStack extends cdk.Stack {
  public readonly monthlyUsageTable: dynamodb.Table;
  public readonly transactionsTable: dynamodb.Table;
  private readonly monthly_limit = 6.6;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const monthlyUsageTable = dynamodb.Table.fromTableName(
      this,
      "MonthlyUsageTable",
      "Bedrock-Monthly-Usage"
    );

    const transactionsTable = dynamodb.Table.fromTableName(
      this,
      "TransactionsTable",
      "Bedrock-Transactions"
    );

    const inferenceUsageFn = new lambda.Function(this, "InferenceUsageFn", {
      runtime: lambda.Runtime.PYTHON_3_10,
      handler: "main.handler",
      code: lambda.Code.fromAsset(
        path.join(__dirname, "..", "functions", "inference-usage-lambda")
      ),
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      environment: {
        MONTHLY_USAGE_TABLE: monthlyUsageTable.tableName,
        MONTHLY_LIMIT: String(this.monthly_limit),
      },
    });

    const proxyFn = new lambda.Function(this, "BedrockProxyFn", {
      runtime: lambda.Runtime.NODEJS_22_X,
      handler: "index.handler",
      code: lambda.Code.fromAsset(
        path.join(__dirname, "..", "functions", "inference-proxy-lambda")
      ),
      timeout: cdk.Duration.seconds(60),
      memorySize: 1024,
      environment: {
        REGION: this.region,
        MONTHLY_USAGE_TABLE: monthlyUsageTable.tableName,
        TRANSACTIONS_TABLE: transactionsTable.tableName,
        MONTHLY_LIMIT: String(this.monthly_limit),
      },
    });

    proxyFn.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,

        actions: [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:PutItem",
        ],

        resources: [
          "arn:aws:bedrock:*:*:foundation-model/anthropic.*",
          "arn:aws:dynamodb:*:*:table/Bedrock-Monthly-Usage",
          "arn:aws:dynamodb:*:*:table/Bedrock-Transactions",
        ],
      })
    );

    inferenceUsageFn.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,

        actions: ["dynamodb:GetItem", "dynamodb:Scan"],

        resources: ["arn:aws:dynamodb:*:*:table/Bedrock-Monthly-Usage"],
      })
    );

    const api = new apigw.RestApi(this, "BedApiGatewayApi", {
      restApiName: "bedrock-gateway-api",
      description: "API Gateway for Bedrock proxy Lambda function",
      deployOptions: {
        stageName: "prod",
        throttlingRateLimit: 20,
      },
      defaultCorsPreflightOptions: {
        allowOrigins: apigw.Cors.ALL_ORIGINS,
        allowMethods: ["POST", "OPTIONS"],
        allowHeaders: [
          "Content-Type",
          "Authorization",
          "x-api-key",
          "Accept",
          "Origin",
          "X-Requested-With",
        ],
        allowCredentials: false,
      },
      minCompressionSize: cdk.Size.bytes(1024),
    });

    api.addGatewayResponse("Default4xx", {
      type: apigw.ResponseType.DEFAULT_4XX,
      responseHeaders: {
        "Access-Control-Allow-Origin": "'*'",
        "Access-Control-Allow-Headers":
          "'Content-Type,Authorization,x-api-key,Accept,Origin,X-Requested-With'",
        "Access-Control-Allow-Methods": "'POST,OPTIONS'",
      },
    });

    api.addGatewayResponse("Default5xx", {
      type: apigw.ResponseType.DEFAULT_5XX,
      responseHeaders: {
        "Access-Control-Allow-Origin": "'*'",
        "Access-Control-Allow-Headers":
          "'Content-Type,Authorization,x-api-key,Accept,Origin,X-Requested-With'",
        "Access-Control-Allow-Methods": "'POST,OPTIONS'",
      },
    });

    const v1 = api.root.addResource("v1");

    const usageLambdaIntegration = new apigw.LambdaIntegration(
      inferenceUsageFn,
      {
        proxy: true,
        allowTestInvoke: true,
      }
    );

    const usage = v1.addResource("usage");
    usage.addMethod("GET", usageLambdaIntegration, {
      apiKeyRequired: false,
    });

    const bedrockProxyFunctionUrl = proxyFn.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.NONE,
      invokeMode: lambda.InvokeMode.RESPONSE_STREAM,
      cors: {
        allowedOrigins: ["*"],
        allowedHeaders: ["*"],
        allowedMethods: [lambda.HttpMethod.POST],
      },
    });

    new cdk.CfnOutput(this, "LambdaFunctionName", {
      value: proxyFn.functionName,
      description:
        "The name of the Lambda function that serves as the Bedrock proxy.",
      exportName: "BedrockProxyFunctionName",
    });

    new cdk.CfnOutput(this, "LambdaFunctionArn", {
      value: proxyFn.functionArn,
      description:
        "The ARN of the Lambda function that serves as the Bedrock proxy.",
      exportName: "BedrockProxyFunctionArn",
    });

    new cdk.CfnOutput(this, "ProxyApiInvokeUrl", {
      value: bedrockProxyFunctionUrl.url,
      description: "POST here to call the proxy.",
      exportName: "BedrockGatewayInvokeUrl",
    });

    new cdk.CfnOutput(this, "UsageApiInvokeUrl", {
      value: `${api.url}v1/usage`,
      description: "GET here to retrieve current monthly usage for a user.",
      exportName: "BedrockUsageInvokeUrl",
    });

    new cdk.CfnOutput(this, "Region", {
      value: this.region,
      description: "AWS Region where the stack is deployed",
      exportName: "BedrockGatewayRegion",
    });

    new cdk.CfnOutput(this, "MonthlyUsageTableName", {
      value: monthlyUsageTable.tableName,
      description: "Name of the Bedrock Monthly Usage table",
      exportName: "BedrockMonthlyUsageTableName",
    });

    new cdk.CfnOutput(this, "MonthlyUsageTableArn", {
      value: monthlyUsageTable.tableArn,
      description: "ARN of the Bedrock Monthly Usage table",
      exportName: "BedrockMonthlyUsageTableArn",
    });

    new cdk.CfnOutput(this, "TransactionsTableName", {
      value: transactionsTable.tableName,
      description: "Name of the Bedrock Transactions table",
      exportName: "BedrockTransactionsTableName",
    });

    new cdk.CfnOutput(this, "TransactionsTableArn", {
      value: transactionsTable.tableArn,
      description: "ARN of the Bedrock Transactions table",
      exportName: "BedrockTransactionsTableArn",
    });
  }
}
