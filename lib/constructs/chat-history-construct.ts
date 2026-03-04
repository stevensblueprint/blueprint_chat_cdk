import { Construct } from "constructs";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as cdk from "aws-cdk-lib";

export interface ChatHistoryConstructProps {
  s3BucketName: string;
  chatHistoryTableName: string;
}

export default class ChatHistoryConstruct extends Construct {
  public readonly chatHistoryTable: dynamodb.ITable;
  public readonly s3Bucket: s3.IBucket;

  constructor(scope: Construct, id: string, props: ChatHistoryConstructProps) {
    super(scope, id);

    this.s3Bucket = s3.Bucket.fromBucketName(
      this,
      "ChatHistoryBucket",
      `${props.s3BucketName}`,
    );

    this.s3Bucket.bucketName;

    this.chatHistoryTable = new dynamodb.Table(this, "ChatHistoryTable", {
      tableName: props.chatHistoryTableName,
      partitionKey: {
        name: "conversationId",
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: { name: "timestamp", type: dynamodb.AttributeType.NUMBER },
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    new cdk.CfnOutput(this, "ChatHistoryTableName", {
      value: this.chatHistoryTable.tableName,
      exportName: "ChatHistoryTableName",
    });
    new cdk.CfnOutput(this, "ChatHistoryBucketName", {
      value: this.s3Bucket.bucketName,
      exportName: "ChatHistoryBucketName",
    });
  }
}
