import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as sqs from "aws-cdk-lib/aws-sqs";
import { SqsEventSource } from "aws-cdk-lib/aws-lambda-event-sources";
import { Construct } from "constructs";
import * as path from "path";

export interface IngestionWorkerConstructProps {
  queue: sqs.IQueue;
  documentBucket: s3.IBucket;
  notionApiKey: string;
  driveApiKey: string;
  wikiApiKey?: string;
  wikiBaseUrl?: string;
}

export class IngestionWorkerConstruct extends Construct {
  public readonly workerFn: lambda.Function;

  constructor(
    scope: Construct,
    id: string,
    props: IngestionWorkerConstructProps,
  ) {
    super(scope, id);

    this.workerFn = new lambda.Function(this, "IngestionWorkerFn", {
      runtime: lambda.Runtime.PYTHON_3_10,
      handler: "main.lambda_handler",
      code: lambda.Code.fromAsset(
        path.join(__dirname, "../../functions/ingestion-worker-lambda"),
        {
          bundling: {
            image: lambda.Runtime.PYTHON_3_10.bundlingImage,
            command: [
              "bash",
              "-c",
              "pip install -r requirements.txt -t /asset-output && cp -au . /asset-output",
            ],
          },
        },
      ),
      timeout: cdk.Duration.minutes(5),
      memorySize: 512,
      environment: {
        DOCUMENT_BUCKET_NAME: props.documentBucket.bucketName,
        NOTION_API_KEY: props.notionApiKey,
        DRIVE_API_KEY: props.driveApiKey,
        WIKI_API_KEY: props.wikiApiKey ?? "",
        WIKI_BASE_URL: props.wikiBaseUrl ?? "",
      },
    });

    props.documentBucket.grantWrite(this.workerFn);

    this.workerFn.addEventSource(
      new SqsEventSource(props.queue, {
        batchSize: 1,
        reportBatchItemFailures: true,
      }),
    );

    new cdk.CfnOutput(this, "IngestionWorkerFnName", {
      value: this.workerFn.functionName,
      description: "Name of the Ingestion Worker Lambda Function",
    });
  }
}