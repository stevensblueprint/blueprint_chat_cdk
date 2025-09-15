import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as path from "path";
import { Construct } from "constructs";
import * as apigw from "aws-cdk-lib/aws-apigateway";
import * as apigatewayv2 from "aws-cdk-lib/aws-apigatewayv2";
import * as integrations from "aws-cdk-lib/aws-apigatewayv2-integrations";

export class BlueprintChatCdkStack extends cdk.Stack {
  public readonly monthlyUsageTable: dynamodb.Table;
  public readonly transactionsTable: dynamodb.Table;
  private readonly projectName = "blueprint-chat";
  private readonly domainName = "sitblueprint.com";
  private readonly subdomainName = "chat";
  // Define monthly usage limits
  private readonly monthly_limit = 6.85;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    /*
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
      }
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
      }
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
    */

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

    const inferenceAuthorizerFn = new lambda.Function(
      this,
      "InferenceAuthorizerFn",
      {
        runtime: lambda.Runtime.PYTHON_3_10,
        handler: "main.handler",
        code: lambda.Code.fromAsset(
          path.join(__dirname, "..", "functions", "inference-authorizer-lambda")
        ),
        timeout: cdk.Duration.seconds(30),
        memorySize: 512,
        environment: {
          MONTHLY_USAGE_TABLE: monthlyUsageTable.tableName,
          MONTHLY_LIMIT: String(this.monthly_limit),
        },
      }
    );

    const inferenceLoggerFn = new lambda.Function(this, "InferenceLoggerFn", {
      runtime: lambda.Runtime.PYTHON_3_10,
      handler: "main.handler",
      code: lambda.Code.fromAsset(
        path.join(__dirname, "..", "functions", "inference-logger-lambda")
      ),
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      environment: {
        MONTHLY_USAGE_TABLE: monthlyUsageTable.tableName,
        TRANSACTIONS_TABLE: transactionsTable.tableName,
      },
    });

    const proxyFn = new lambda.Function(this, "BedrockProxyFn", {
      runtime: lambda.Runtime.PYTHON_3_10,
      handler: "main.handler",
      code: lambda.Code.fromAsset(
        path.join(__dirname, "..", "functions", "inference-proxy-lambda")
      ),
      timeout: cdk.Duration.seconds(60),
      memorySize: 1024,
      environment: {
        GLOBAL_MAX_TOKENS_PER_CALL: "1024",
        REGION: this.region,
      },
    });

    proxyFn.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,

        actions: [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
        ],

        resources: ["arn:aws:bedrock:*:*:foundation-model/anthropic.*"],
      })
    );

    inferenceAuthorizerFn.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,

        actions: ["dynamodb:GetItem", "dynamodb:Scan"],

        resources: ["arn:aws:dynamodb:*:*:table/Bedrock-Monthly-Usage"],
      })
    );

    inferenceLoggerFn.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,

        actions: [
          "dynamodb:GetItem",
          "dynamodb:Scan",
          "dynamodb:UpdateItem",
          "dynamodb:PutItem",
        ],

        resources: [
          "arn:aws:dynamodb:*:*:table/Bedrock-Monthly-Usage",
          "arn:aws:dynamodb:*:*:table/Bedrock-Transactions",
        ],
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

    const httpApi = new apigatewayv2.HttpApi(this, "GatewayAPIv2", {
      apiName: "gateway-api-v2",
    });

    const lambdaIntegration = new integrations.HttpLambdaIntegration(
      "LambdaIntegration",
      proxyFn
    );

    httpApi.addRoutes({
      path: "/chat",
      methods: [apigatewayv2.HttpMethod.POST],
      integration: lambdaIntegration,
    });

    const v1 = api.root.addResource("v1");

    const authorizerLambdaIntegration = new apigw.LambdaIntegration(
      inferenceAuthorizerFn,
      {
        proxy: true,
        allowTestInvoke: true,
      }
    );

    const authorize = v1.addResource("authorize");
    authorize.addMethod("POST", authorizerLambdaIntegration, {
      apiKeyRequired: false,
    });

    const loggerLambdaIntegration = new apigw.LambdaIntegration(
      inferenceLoggerFn,
      {
        proxy: true,
        allowTestInvoke: true,
      }
    );

    const log = v1.addResource("log");
    log.addMethod("POST", loggerLambdaIntegration, {
      apiKeyRequired: false,
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
      value: `${httpApi.url}chat`,
      description: "POST here to call the proxy.",
      exportName: "BedrockGatewayInvokeUrl",
    });

    new cdk.CfnOutput(this, "AuthorizerApiInvokeUrl", {
      value: `${api.url}v1/authorize`,
      description: "POST here to call the authorizer.",
      exportName: "BedrockAuthorizerInvokeUrl",
    });

    new cdk.CfnOutput(this, "LoggerApiInvokeUrl", {
      value: `${api.url}v1/log`,
      description: "POST here to call the logger.",
      exportName: "BedrockLoggerInvokeUrl",
    });

    new cdk.CfnOutput(this, "Region", {
      value: this.region,
      description: "AWS Region where the stack is deployed",
      exportName: "BedrockGatewayRegion",
    });

    /*
    this.monthlyUsageTable.grantReadWriteData(inferenceAuthorizerFn);
    this.transactionsTable.grantReadWriteData(inferenceAuthorizerFn);
    */

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
