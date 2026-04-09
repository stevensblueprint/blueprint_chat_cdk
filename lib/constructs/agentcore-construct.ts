import * as agentcore from "@aws-cdk/aws-bedrock-agentcore-alpha";
import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { NodejsFunction } from "aws-cdk-lib/aws-lambda-nodejs";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3n from "aws-cdk-lib/aws-s3-notifications";
import { createHash } from "crypto";
import { Construct } from "constructs";
import * as path from "path";

export interface AgentCoreConstructProps {
  documentBucket: s3.IBucket;
  chatHistoryTable: dynamodb.ITable;
  environment?: string;
  modelId?: string;
}

export default class AgentCoreConstruct extends Construct {
  public readonly agentProxyFn: lambda.Function;
  public readonly runtimeArn: string;
  public readonly streamingUrl: lambda.FunctionUrl;

  constructor(scope: Construct, id: string, props: AgentCoreConstructProps) {
    super(scope, id);

    const runtimeBaseName = "DocumentQAAgent";
    const maxRuntimeNameLength = 48;
    const rawEnvironment = props.environment?.trim().toLowerCase();
    const normalizedEnvironment =
      rawEnvironment === undefined ||
      rawEnvironment === "" ||
      rawEnvironment === "prod"
        ? "prod"
        : rawEnvironment;
    const envSuffix =
      normalizedEnvironment === "prod" ? "" : `-${normalizedEnvironment}`;

    let runtimeSuffix = "";
    if (normalizedEnvironment !== "prod") {
      const sanitizedEnvironment = normalizedEnvironment
        .replace(/[^A-Za-z0-9_]/g, "_")
        .replace(/_+/g, "_")
        .replace(/^_+|_+$/g, "");

      if (sanitizedEnvironment.length === 0) {
        throw new Error(
          "AgentCoreConstruct: environment must contain at least one alphanumeric character or underscore after sanitization.",
        );
      }

      const maxSuffixLen = maxRuntimeNameLength - runtimeBaseName.length;
      if (maxSuffixLen < 0) {
        throw new Error(
          `AgentCoreConstruct: runtime base name '${runtimeBaseName}' exceeds ${maxRuntimeNameLength} characters.`,
        );
      }

      if (sanitizedEnvironment.length + 1 <= maxSuffixLen) {
        runtimeSuffix = `_${sanitizedEnvironment}`;
      } else {
        const disambiguatorLength = 6;
        const disambiguator = createHash("sha256")
          .update(sanitizedEnvironment)
          .digest("hex")
          .slice(0, disambiguatorLength);
        const maxPrefixLen = Math.max(
          0,
          maxSuffixLen - 1 - disambiguatorLength,
        );
        const truncatedPrefix = sanitizedEnvironment.slice(0, maxPrefixLen);
        runtimeSuffix = `_${truncatedPrefix}${disambiguator}`;
      }
    }

    const runtimeNameCandidate = `${runtimeBaseName}${runtimeSuffix}`;
    const runtimeName = /^[A-Za-z]/.test(runtimeNameCandidate)
      ? runtimeNameCandidate
      : `A${runtimeNameCandidate}`.slice(0, maxRuntimeNameLength);

    if (!/^[A-Za-z][A-Za-z0-9_]{0,47}$/.test(runtimeName)) {
      throw new Error(
        `AgentCoreConstruct: runtimeName '${runtimeName}' is invalid. Must match [a-zA-Z][a-zA-Z0-9_]{0,47}.`,
      );
    }

    const modelId =
      props.modelId ?? "us.anthropic.claude-3-5-haiku-20241022-v1:0";
    const embeddingModelId = "amazon.titan-embed-text-v2:0";
    const region = cdk.Stack.of(this).region;
    const account = cdk.Stack.of(this).account;

    const vectorBucketName = `${cdk.Stack.of(this).stackName.toLowerCase()}-doc-vectors`;
    const vectorIndexName = "documents";

    const vectorBucket = new cdk.CfnResource(this, "VectorBucket", {
      type: "AWS::S3Vectors::VectorBucket",
      properties: { VectorBucketName: vectorBucketName },
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
        resources: [
          `arn:aws:bedrock:${region}::foundation-model/${embeddingModelId}`,
        ],
      }),
    );
    indexerFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "s3vectors:PutVectors",
          "s3vectors:DeleteVectors",
          "s3vectors:ListVectors",
        ],
        resources: [vectorsArn],
      }),
    );

    const artifact = agentcore.AgentRuntimeArtifact.fromAsset(
      path.join(__dirname, "../../agent"),
    );

    const runtime = new agentcore.Runtime(this, "DocQARuntime", {
      runtimeName,
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
      description: `Default endpoint for DocumentQAAgent${envSuffix}`,
    });

    this.runtimeArn = runtime.agentRuntimeArn;

    props.chatHistoryTable.grantReadWriteData(runtime);

    const baseModelId = modelId.replace(/^[a-z]+\./, "");

    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
        ],
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

    const agentProxyEntry = path.join(
      __dirname,
      "../../functions/agentcore-proxy-lambda/index.ts",
    );

    const agentProxyEnvironment = {
      AGENT_RUNTIME_ARN: runtime.agentRuntimeArn,
      AGENT_RUNTIME_ENDPOINT: endpoint.endpointName,
      REGION: region,
    };

    this.agentProxyFn = new NodejsFunction(this, "AgentProxyFn", {
      runtime: lambda.Runtime.NODEJS_22_X,
      entry: agentProxyEntry,
      handler: "handler",
      timeout: cdk.Duration.seconds(60),
      environment: agentProxyEnvironment,
    });

    runtime.grantInvoke(this.agentProxyFn);

    const agentStreamingFn = new NodejsFunction(this, "AgentStreamingFn", {
      runtime: lambda.Runtime.NODEJS_22_X,
      entry: agentProxyEntry,
      handler: "streamingHandler",
      timeout: cdk.Duration.seconds(60),
      environment: agentProxyEnvironment,
    });

    runtime.grantInvoke(agentStreamingFn);

    this.streamingUrl = agentStreamingFn.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.NONE,
      invokeMode: lambda.InvokeMode.RESPONSE_STREAM,
      cors: {
        allowedOrigins: ["*"],
        allowedHeaders: ["content-type", "authorization"],
        allowedMethods: [lambda.HttpMethod.ALL],
      },
    });
    cdk.Tags.of(this).add("agentcore", "document-qa-agent");
  }
}
