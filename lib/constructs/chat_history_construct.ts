import { Construct } from "constructs";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as s3 from "aws-cdk-lib/aws-s3";
import { NodejsFunction } from "aws-cdk-lib/aws-lambda-nodejs";
import * as cdk from "aws-cdk-lib";
import * as path from "path";

export interface ChatHistoryConstructProps {
  s3BucketName: string;
  chatHistoryTableName: string;
}

export default class ChatHistoryConstruct extends Construct {
  public readonly chatHistoryTable: dynamodb.ITable;
  public readonly s3Bucket: s3.IBucket;
  public readonly chatHistoryLambda: NodejsFunction;

  constructor(scope: Construct, id: string, props: ChatHistoryConstructProps) {
    super(scope, id);

    // S3 Bucket Data Model
    this.s3Bucket = new s3.Bucket(this, "ChatHistoryBucket", {
      bucketName: `${cdk.Stack.of(this).account.toLowerCase()}-${props.s3BucketName}`,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
    });

    // DynamoDB Data Model
    this.chatHistoryTable = new dynamodb.TableV2(this, "ChatHistoryTable", {
      tableName: props.chatHistoryTableName,
      partitionKey: {
        name: "userId",
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: "conversationId",
        type: dynamodb.AttributeType.STRING,
      },
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      billing: dynamodb.Billing.onDemand(),
    });

    // Chat History Lambda implementation
    this.chatHistoryLambda = new NodejsFunction(this, "ChatHistoryLambda", {
      entry: path.join(
        __dirname,
        "../../functions/chat-history-lambda/index.ts",
      ),
      handler: "handler",
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      environment: {
        DYNAMODB_TABLE: this.chatHistoryTable.tableName,
        S3_BUCKET: this.s3Bucket.bucketName,
      },
    });

    // Grant required read/write permissions
    this.chatHistoryTable.grantReadWriteData(this.chatHistoryLambda);
    this.s3Bucket.grantReadWrite(this.chatHistoryLambda);

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
