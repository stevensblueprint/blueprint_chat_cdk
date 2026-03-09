import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as path from "path";
import { Construct } from "constructs";
import * as agentcore from "@aws-cdk/aws-bedrock-agentcore-alpha";

export interface AgentCoreConstructProps {
  documentBucket: s3.IBucket;
  chatHistoryTable: dynamodb.ITable;
  modelId?: string;
}

export default class AgentCoreConstruct extends Construct {
  public readonly agentProxyFn: lambda.Function;
  public readonly runtimeArn: string;

  constructor(scope: Construct, id: string, props: AgentCoreConstructProps) {
    super(scope, id);

    const modelId = props.modelId ?? "anthropic.claude-3-haiku-20240307-v1:0";
    const region = cdk.Stack.of(this).region;

    const artifact = agentcore.AgentRuntimeArtifact.fromAsset(
      path.join(__dirname, "../../agent"),
    );

    const runtime = new agentcore.Runtime(this, "DocQARuntime", {
      runtimeName: "DocumentQAAgent",
      agentRuntimeArtifact: artifact,
      networkConfiguration:
        agentcore.RuntimeNetworkConfiguration.usingPublicNetwork(),
      environmentVariables: {
        DOCUMENT_BUCKET: props.documentBucket.bucketName,
        CHAT_HISTORY_TABLE: props.chatHistoryTable.tableName,
        BEDROCK_MODEL_ID: modelId,
      },
    });

    this.runtimeArn = runtime.agentRuntimeArn;

    props.documentBucket.grantRead(runtime);
    props.chatHistoryTable.grantReadWriteData(runtime);
    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["bedrock:InvokeModel"],
        resources: [`arn:aws:bedrock:${region}::foundation-model/${modelId}`],
      }),
    );

    this.agentProxyFn = new lambda.Function(this, "AgentProxyFn", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "main.handler",
      code: lambda.Code.fromAsset(
        path.join(__dirname, "../../functions/agentcore-proxy-lambda"),
      ),
      timeout: cdk.Duration.seconds(60),
      environment: {
        AGENT_RUNTIME_ARN: runtime.agentRuntimeArn,
        REGION: region,
      },
    });

    runtime.grantInvoke(this.agentProxyFn);
  }
}