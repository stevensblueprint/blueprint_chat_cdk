import * as sqs from "aws-cdk-lib/aws-sqs";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as cloudwatch_actions from "aws-cdk-lib/aws-cloudwatch-actions";
import * as sns from "aws-cdk-lib/aws-sns";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";

export interface IngestionQueueConstructProps {
  /**
   * SNS topic to notify when messages land in the DLQ.
   * If omitted, the alarm is still created but no notification action is wired.
   */
  alarmTopic?: sns.ITopic;

  /**
   * Maximum number of receive attempts before a message is moved to the DLQ.
   */
  maxReceiveCount?: number;

  /**
   * How long the main IngestionQueue retains unprocessed messages.
   */
  retentionPeriod?: cdk.Duration;
}

/**
 * IngestionQueueConstruct
 */
export class IngestionQueueConstruct extends Construct {
  
  public readonly queue: sqs.Queue;

  public readonly dlq: sqs.Queue;

  /** CloudWatch alarm **/
  public readonly dlqAlarm: cloudwatch.Alarm;

  constructor(
    scope: Construct,
    id: string,
    props: IngestionQueueConstructProps = {},
  ) {
    super(scope, id);

    const {
      alarmTopic,
      maxReceiveCount = 3,
      retentionPeriod = cdk.Duration.days(4),
    } = props;

    // Dead-Letter Queue 
    this.dlq = new sqs.Queue(this, "IngestionDLQ", {
      queueName: "IngestionDLQ",
      retentionPeriod: cdk.Duration.days(14),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
    });

    // Main Ingestion Queue
    this.queue = new sqs.Queue(this, "IngestionQueue", {
      queueName: "IngestionQueue",
      retentionPeriod,
      visibilityTimeout: cdk.Duration.minutes(5),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      deadLetterQueue: {
        queue: this.dlq,
        maxReceiveCount,
      },
    });

    // ── DLQ CloudWatch Alarm ─────────────────────────────────────────────────
    // Threshold = 1: any DLQ depth > 0 is always worth investigating immediately.
    this.dlqAlarm = new cloudwatch.Alarm(this, "DLQDepthAlarm", {
      alarmName: "IngestionDLQ-MessagesVisible",
      alarmDescription:
        "One or more messages have been dead-lettered. " +
        "Investigate, apply a fix, then redrive or discard.",
      metric: this.dlq.metricApproximateNumberOfMessagesVisible({
        period: cdk.Duration.minutes(1),
        statistic: "Sum",
      }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator:
        cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    if (alarmTopic) {
      this.dlqAlarm.addAlarmAction(
        new cloudwatch_actions.SnsAction(alarmTopic),
      );
    }

    new cdk.CfnOutput(this, "IngestionQueueUrl", {
      value: this.queue.queueUrl,
      description: "URL of the main Ingestion SQS Queue",
    });

    new cdk.CfnOutput(this, "IngestionDLQUrl", {
      value: this.dlq.queueUrl,
      description: "URL of the Ingestion Dead-Letter Queue",
    });
  }
}