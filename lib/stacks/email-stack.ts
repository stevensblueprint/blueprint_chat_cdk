import * as cdk from "aws-cdk-lib";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";

export interface EmailStackProps extends cdk.StackProps {
  readonly bucketName: string;
}

export class EmailStack extends cdk.Stack {
  public readonly emailBucket: s3.Bucket;
  constructor(scope: Construct, id: string, props: EmailStackProps) {
    super(scope, id, props);

    this.emailBucket = new s3.Bucket(this, "EmailBucket", {
      bucketName: props.bucketName,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    this.emailBucket.addToResourcePolicy(
      new cdk.aws_iam.PolicyStatement({
        actions: ["s3:PutObject"],
        resources: [this.emailBucket.arnForObjects("*")],
        principals: [new cdk.aws_iam.ServicePrincipal("ses.amazonaws.com")],
      }),
    );
  }
}
