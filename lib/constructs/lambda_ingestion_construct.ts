import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as lambdaEventSources from 'aws-cdk-lib/aws-lambda-event-sources';
import { Construct } from 'constructs';
import * as path from 'path';

export interface LambdaIngestionConstructProps {
  /**
   * The SQS queue to consume messages from.
   */
  ingestionQueue: sqs.IQueue;
}

export default class LambdaIngestionConstruct extends Construct {
    constructor(scope: Construct, 
        id: string, 
        props: LambdaIngestionConstructProps
    ) {
        super(scope, id);

        const ingestionWorkerFn = new lambda.Function(this, 'IngestionWorkerFn', {
            runtime: lambda.Runtime.PYTHON_3_10,
            handler: 'main.lambda_handler',
            code: lambda.Code.fromAsset(
                path.join(__dirname, '..', '..', 'functions', 'ingestion-worker-lambda')
            ),
            timeout: cdk.Duration.seconds(30),
            memorySize: 512,
        });

        ingestionWorkerFn.addEventSource(new lambdaEventSources.SqsEventSource(props.ingestionQueue, {
            batchSize: 10,
            reportBatchItemFailures: true,
        }));

        new cdk.CfnOutput(this, 'IngestionWorkerFunctionName', {
            value: ingestionWorkerFn.functionName,
            description: 'The name of the Lambda function that processes ingestion messages from SQS.',
            exportName: 'IngestionWorkerFunctionName',

        });

        new cdk.CfnOutput(this, 'IngestionWorkerFunctionArn', {
            value: ingestionWorkerFn.functionArn,
            description: 'The ARN of the Lambda function that processes ingestion messages from SQS.',
            exportName: 'IngestionWorkerFunctionArn',
        });
    }

}