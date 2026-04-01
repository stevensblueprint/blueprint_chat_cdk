import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as sqs from "aws-cdk-lib/aws-sqs";
import * as lambdaEventSources from "aws-cdk-lib/aws-lambda-event-sources";
import { Construct } from "constructs";
import * as path from "path";
import * as crypto from "crypto";
import * as fs from "fs";

export interface LambdaIngestionConstructProps {
  ingestionQueue: sqs.IQueue;
  documentBucket: s3.IBucket;
  notionApiKey: string;
  driveApiKey: string;
  wikiApiKey: string;
  wikiBaseUrl: string;
}

export default class LambdaIngestionConstruct extends Construct {
  constructor(
    scope: Construct,
    id: string,
    props: LambdaIngestionConstructProps,
  ) {
    super(scope, id);

    const lambdaDir = path.join(
      __dirname,
      "..",
      "..",
      "functions",
      "ingestion-worker-lambda",
    );

    // Layer is only rebuilt when requirements.txt content changes.
    // No Docker needed for code-only deploys.
    const requirementsHash = crypto
      .createHash("sha256")
      .update(fs.readFileSync(path.join(lambdaDir, "requirements.txt")))
      .digest("hex");

    const depsLayer = new lambda.LayerVersion(this, "IngestionDepsLayer", {
      description: "Ingestion worker pip dependencies (pydantic, requests)",
      compatibleRuntimes: [lambda.Runtime.PYTHON_3_10],
      code: lambda.Code.fromAsset(lambdaDir, {
        assetHashType: cdk.AssetHashType.CUSTOM,
        assetHash: requirementsHash,
        bundling: {
          image: lambda.Runtime.PYTHON_3_10.bundlingImage,
          command: [
            "bash",
            "-c",
            "pip install -r requirements.txt -t /asset-output/python",
          ],
        },
      }),
    });

    const ingestionWorkerFn = new lambda.Function(this, "IngestionWorkerFn", {
      runtime: lambda.Runtime.PYTHON_3_10,
      handler: "main.lambda_handler",
      // Plain asset — no bundling, no Docker required for code changes.
      code: lambda.Code.fromAsset(lambdaDir, {
        exclude: ["tests/**", "requirements*.txt", "__pycache__/**", "*.pyc"],
      }),
      layers: [depsLayer],
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      environment: {
        DOCUMENT_BUCKET_NAME: props.documentBucket.bucketName,
        NOTION_API_KEY: props.notionApiKey,
        DRIVE_API_KEY: props.driveApiKey,
        WIKI_API_KEY: props.wikiApiKey,
        WIKI_BASE_URL: props.wikiBaseUrl,
      },
    });

    props.documentBucket.grantReadWrite(ingestionWorkerFn);

    ingestionWorkerFn.addEventSource(
      new lambdaEventSources.SqsEventSource(props.ingestionQueue, {
        batchSize: 10,
        reportBatchItemFailures: true,
      }),
    );

    new cdk.CfnOutput(this, "IngestionWorkerFunctionName", {
      value: ingestionWorkerFn.functionName,
      description:
        "The name of the Lambda function that processes ingestion messages from SQS.",
      exportName: "IngestionWorkerFunctionName",
    });

    new cdk.CfnOutput(this, "IngestionWorkerFunctionArn", {
      value: ingestionWorkerFn.functionArn,
      description:
        "The ARN of the Lambda function that processes ingestion messages from SQS.",
      exportName: "IngestionWorkerFunctionArn",
    });
  }
}
