import * as cdk from "aws-cdk-lib";
import * as apigw from "aws-cdk-lib/aws-apigateway";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Construct } from "constructs";
import * as path from "path";

export interface LambdaLlmProxyConstructProps {
  /**
   * The monthly limit for usage in USD.
   */
  monthlyLimit?: number;

  environment?: string;
}

export default class LambdaLlmProxyConstruct extends Construct {
  public readonly monthlyUsageTable: dynamodb.ITable;
  public readonly transactionsTable: dynamodb.ITable;
  public readonly api: apigw.RestApi;
  public readonly v1Resource: apigw.IResource;

  constructor(
    scope: Construct,
    id: string,
    props: LambdaLlmProxyConstructProps,
  ) {
    super(scope, id);

    const rawEnvironment = props.environment?.trim().toLowerCase();
    const normalizedEnvironment =
      rawEnvironment === undefined || rawEnvironment === "" || rawEnvironment === "prod"
        ? "prod"
        : rawEnvironment;
    const envSuffix =
      normalizedEnvironment === "prod" ? "" : `-${normalizedEnvironment}`;
    const monthlyUsageTableName = `Bedrock-Monthly-Usage${envSuffix}`;
    const transactionsTableName = `Bedrock-Transactions${envSuffix}`;

    this.monthlyUsageTable = dynamodb.Table.fromTableName(
      this,
      "MonthlyUsageTable",
      monthlyUsageTableName,
    );

    this.transactionsTable = dynamodb.Table.fromTableName(
      this,
      "TransactionsTable",
      transactionsTableName,
    );

    const inferenceUsageFn = new lambda.Function(this, "InferenceUsageFn", {
      runtime: lambda.Runtime.PYTHON_3_10,
      handler: "main.handler",
      code: lambda.Code.fromAsset(
        path.join(__dirname, "..", "..", "functions", "inference-usage-lambda"),
      ),
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      environment: {
        MONTHLY_USAGE_TABLE: this.monthlyUsageTable.tableName,
        MONTHLY_LIMIT: String(props.monthlyLimit),
      },
    });

    const proxyFn = new lambda.Function(this, "BedrockProxyFn", {
      runtime: lambda.Runtime.NODEJS_22_X,
      handler: "index.handler",
      code: lambda.Code.fromAsset(
        path.join(__dirname, "..", "..", "functions", "inference-proxy-lambda"),
      ),
      timeout: cdk.Duration.seconds(60),
      memorySize: 1024,
      environment: {
        REGION: cdk.Stack.of(this).region,
        MONTHLY_USAGE_TABLE: this.monthlyUsageTable.tableName,
        TRANSACTIONS_TABLE: this.transactionsTable.tableName,
        MONTHLY_LIMIT: String(props.monthlyLimit),
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
          this.monthlyUsageTable.tableArn,
          this.transactionsTable.tableArn,
        ],
      }),
    );

    inferenceUsageFn.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["dynamodb:GetItem", "dynamodb:Scan"],
        resources: [this.monthlyUsageTable.tableArn],
      }),
    );

    this.api = new apigw.RestApi(this, "BedUsageApi", {
      restApiName: `bedrock-usage-api${envSuffix}`,
      description: "API Gateway for monthly usage statistics",
      deployOptions: {
        stageName: normalizedEnvironment,
        throttlingRateLimit: 20,
      },
      defaultCorsPreflightOptions: {
        allowOrigins: apigw.Cors.ALL_ORIGINS,
        allowMethods: ["GET", "POST", "OPTIONS"],
        allowHeaders: [
          "Content-Type",
          "Authorization",
          "Accept",
          "Origin",
          "X-Requested-With",
        ],
        allowCredentials: false,
      },
      minCompressionSize: cdk.Size.bytes(1024),
    });

    this.api.addGatewayResponse("Default4xx", {
      type: apigw.ResponseType.DEFAULT_4XX,
      responseHeaders: {
        "Access-Control-Allow-Origin": "'*'",
        "Access-Control-Allow-Headers":
          "'Content-Type,Authorization,x-api-key,Accept,Origin,X-Requested-With'",
        "Access-Control-Allow-Methods": "'GET,POST,OPTIONS'",
      },
    });

    this.api.addGatewayResponse("Default5xx", {
      type: apigw.ResponseType.DEFAULT_5XX,
      responseHeaders: {
        "Access-Control-Allow-Origin": "'*'",
        "Access-Control-Allow-Headers":
          "'Content-Type,Authorization,x-api-key,Accept,Origin,X-Requested-With'",
        "Access-Control-Allow-Methods": "'GET,POST,OPTIONS'",
      },
    });

    this.v1Resource = this.api.root.addResource("v1");
    const v1 = this.v1Resource;

    const usageLambdaIntegration = new apigw.LambdaIntegration(
      inferenceUsageFn,
      {
        proxy: true,
        allowTestInvoke: true,
      },
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
      exportName: `BedrockProxyFunctionName${envSuffix}`,
    });

    new cdk.CfnOutput(this, "LambdaFunctionArn", {
      value: proxyFn.functionArn,
      description:
        "The ARN of the Lambda function that serves as the Bedrock proxy.",
      exportName: `BedrockProxyFunctionArn${envSuffix}`,
    });

    new cdk.CfnOutput(this, "ProxyApiInvokeUrl", {
      value: bedrockProxyFunctionUrl.url,
      description: "POST here to call the proxy.",
      exportName: `BedrockGatewayInvokeUrl${envSuffix}`,
    });

    new cdk.CfnOutput(this, "UsageApiInvokeUrl", {
      value: `${this.api.url}v1/usage`,
      description: "GET here to retrieve current monthly usage for a user.",
      exportName: `BedrockUsageInvokeUrl${envSuffix}`,
    });

    new cdk.CfnOutput(this, "Region", {
      value: cdk.Stack.of(this).region,
      description: "AWS Region where the stack is deployed",
      exportName: `BedrockGatewayRegion${envSuffix}`,
    });

    new cdk.CfnOutput(this, "MonthlyUsageTableName", {
      value: this.monthlyUsageTable.tableName,
      description: "Name of the Bedrock Monthly Usage table",
      exportName: `BedrockMonthlyUsageTableName${envSuffix}`,
    });

    new cdk.CfnOutput(this, "MonthlyUsageTableArn", {
      value: this.monthlyUsageTable.tableArn,
      description: "ARN of the Bedrock Monthly Usage table",
      exportName: `BedrockMonthlyUsageTableArn${envSuffix}`,
    });

    new cdk.CfnOutput(this, "TransactionsTableName", {
      value: this.transactionsTable.tableName,
      description: "Name of the Bedrock Transactions table",
      exportName: `BedrockTransactionsTableName${envSuffix}`,
    });

    new cdk.CfnOutput(this, "TransactionsTableArn", {
      value: this.transactionsTable.tableArn,
      description: "ARN of the Bedrock Transactions table",
      exportName: `BedrockTransactionsTableArn${envSuffix}`,
    });
  }
}
