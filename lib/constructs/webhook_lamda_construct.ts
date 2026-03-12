import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as sqs from "aws-cdk-lib/aws-sqs";
import { Construct } from "constructs";
import * as path from "path";

export interface WebhookLambdaConstructProps {
  /**
   * Code Path
   */
  codePath: string;

  /**
   * Description
   */
  description: string;

  /**
   * Document Bucket
   * /
   */
  documentBucket: s3.IBucket;

  /**
   * Environment Variables
   */
  environmentVariables: { [key: string]: string };

  /**
   * Webhook Events Queue
   */
  webhookEventsQueue: sqs.IQueue;
}

export default class WebhookLambdaConstruct extends Construct {
  constructor(
    scope: Construct,
    id: string,
    props: WebhookLambdaConstructProps,
  ) {
    super(scope, id);
    const webhookListenerFn = new lambda.Function(this, "WebhookListenerFn", {
      runtime: lambda.Runtime.PYTHON_3_10,
      handler: "main.handler",
      code: lambda.Code.fromAsset(
        path.join(__dirname, "..", "..", props.codePath),
      ),
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      environment: {
        ...props.environmentVariables,
        DOCUMENT_BUCKET: props.documentBucket.bucketName,
        WEBHOOK_EVENTS_QUEUE_URL: props.webhookEventsQueue.queueUrl,
      },
      description: props.description,
    });

    props.documentBucket.grantReadWrite(webhookListenerFn);
    props.webhookEventsQueue.grantSendMessages(webhookListenerFn);

    const fnUrl = webhookListenerFn.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.NONE,
      cors: {
        allowedOrigins: ["*"],
        allowedMethods: [lambda.HttpMethod.POST],
      },
    });

    new cdk.CfnOutput(this, "WebhookListenerFnArn", {
      value: webhookListenerFn.functionArn,
      description: "ARN of the Webhook Listener Lambda Function",
    });
    new cdk.CfnOutput(this, "WebhookListenerFnUrl", {
      value: fnUrl.url,
      description: "URL of the Webhook Listener Lambda Function",
    });
  }
}
