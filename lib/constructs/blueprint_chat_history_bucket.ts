import * as cdk from "aws-cdk-lib";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";

export class ChatHistoryBucketConstruct extends Construct 
{
    public readonly bucket: s3.Bucket;

    constructor(scope: Construct, id: string)
    {
        super(scope, id);

        this.bucket = new s3.Bucket(this, "ChatHistroyBucket", {
            bucketName: "blueprint-chat-history",
            blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
            removalPolicy: cdk.RemovalPolicy.RETAIN,
            
            enforceSSL: true,
            encryption: s3.BucketEncryption.S3_MANAGED,
            versioned: true,
            autoDeleteObjects: false,
        });

        new cdk.CfnOutput(this, "ChatHistoryBucket", {
            value: this.bucket.bucketName,
            exportName: "ChatHistoryBucketName",
        });
    }

    static threadKey(userId: string, conversationId: string): string
    {
        return `users/${userId}/conversations/${conversationId}/thread.json`;
    }

}