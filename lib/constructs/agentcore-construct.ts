import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3n from "aws-cdk-lib/aws-s3-notifications";
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
  public readonly streamingUrl: lambda.FunctionUrl;

  constructor(scope: Construct, id: string, props: AgentCoreConstructProps) {
    super(scope, id);

    const modelId = props.modelId ?? "us.anthropic.claude-3-5-haiku-20241022-v1:0";
    const embeddingModelId = "amazon.titan-embed-text-v2:0";
    const region = cdk.Stack.of(this).region;
    const account = cdk.Stack.of(this).account;

    const vectorBucketName = `${cdk.Stack.of(this).stackName.toLowerCase()}-doc-vectors`;
    const vectorIndexName = "documents";

    const vectorBucket = new cdk.CfnResource(this, "VectorBucket", {
      type: "AWS::S3Vectors::VectorBucket",
      properties: {
        VectorBucketName: vectorBucketName,
      },
    });

    const vectorIndex = new cdk.CfnResource(this, "VectorIndex", {
      type: "AWS::S3Vectors::Index",
      properties: {
        VectorBucketName: vectorBucketName,
        IndexName: vectorIndexName,
        DataType: "float32",
        Dimension: 1024,
        DistanceMetric: "cosine",
      },
    });
    vectorIndex.addDependency(vectorBucket);

    const vectorsArn = `arn:aws:s3vectors:${region}:${account}:bucket/${vectorBucketName}`;

    const indexerFn = new lambda.Function(this, "DocumentIndexerFn", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "main.handler",
      code: lambda.Code.fromAsset(
        path.join(__dirname, "../../functions/document-indexer"),
      ),
      timeout: cdk.Duration.minutes(5),
      environment: {
        VECTOR_BUCKET_NAME: vectorBucketName,
        VECTOR_INDEX_NAME: vectorIndexName,
        EMBEDDING_MODEL_ID: embeddingModelId,
        REGION: region,
      },
    });

    props.documentBucket.grantRead(indexerFn);
    props.documentBucket.addEventNotification(
      s3.EventType.OBJECT_CREATED,
      new s3n.LambdaDestination(indexerFn),
    );
    props.documentBucket.addEventNotification(
      s3.EventType.OBJECT_REMOVED,
      new s3n.LambdaDestination(indexerFn),
    );

    indexerFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["bedrock:InvokeModel"],
        resources: [`arn:aws:bedrock:${region}::foundation-model/${embeddingModelId}`],
      }),
    );
    indexerFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["s3vectors:PutVectors", "s3vectors:DeleteVectors", "s3vectors:ListVectors"],
        resources: [vectorsArn],
      }),
    );

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
        EMBEDDING_MODEL_ID: embeddingModelId,
        VECTOR_BUCKET_NAME: vectorBucketName,
        VECTOR_INDEX_NAME: vectorIndexName,
      },
    });

    const endpoint = runtime.addEndpoint("DefaultEndpoint", {
      description: "Default endpoint for DocumentQAAgent",
    });

    this.runtimeArn = runtime.agentRuntimeArn;

    props.chatHistoryTable.grantReadWriteData(runtime);

    const baseModelId = modelId.replace(/^[a-z]+\./, "");

    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        resources: [
          `arn:aws:bedrock:${region}:*:inference-profile/${modelId}`,
          `arn:aws:bedrock:*::foundation-model/${baseModelId}`,
          `arn:aws:bedrock:${region}::foundation-model/${embeddingModelId}`,
        ],
      }),
    );
    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["s3vectors:QueryVectors"],
        resources: [vectorsArn],
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
        AGENT_RUNTIME_ENDPOINT: endpoint.endpointName,
        REGION: region,
      },
    });

    runtime.grantInvoke(this.agentProxyFn);

    this.streamingUrl = this.agentProxyFn.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.NONE,
      invokeMode: lambda.InvokeMode.RESPONSE_STREAM,
      cors: {
        allowedOrigins: ["*"],
        allowedHeaders: ["content-type", "authorization"],
        allowedMethods: [lambda.HttpMethod.POST, lambda.HttpMethod.OPTIONS],
      },
    });
  }
}
