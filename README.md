# Blueprint Chat CDK

This is a CDK project for Stevens Blueprint Chat.

## Bedrock Inference Proxy

The Bedrock inference proxy allows clients to access AWS Bedrock through a proxy. Each user has an IAM user registered in the hosted AWS account, used strictly for authentication. Users cannot call Bedrock directly with the provided IAM credentials.
The only current supported Bedrock call is `ConverseStream`. Invoking the model without a streamed response is not supported.

The proxy can be accessed at [this link](https://ipqokvoqztcvq245crjkyrbqtm0sxhal.lambda-url.us-east-1.on.aws/). The endpoint is exposed through the Lambda Function URL directly and not through API Gateway, as API Gateway does not support streaming responses.

Takes in a `ConverseStreamCommand` object. More info can be found [here](https://docs.aws.amazon.com/AWSJavaScriptSDK/v3/latest/client/bedrock-runtime/command/ConverseStreamCommand/). For example:

```
{
    "modelId": "anthropic.claude-3-haiku-20240307-v1:0",
    "messages": [
        {
            "role": "user",
            "content": [
                {
                    "text": "Hello World!"
                }
            ]
        }
    ],
    "system": [
        {
            "text": "<system_prompt_here></system_prompt_here>"
        }
    ],
    "inferenceConfig": {
        "maxTokens": 4096,
        "temperature": 0.5
    },
    "additionalModelRequestFields": {}
}
```

Outputs a [streamed response](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_ConverseStream.html) from AWS Bedrock.

## Monthly Usage Statistics

The monthly usage endpoint, exposed through API Gateway, allows clients to retrieve a user's monthly usage statistics in dollars ($USD). Authentication is not required to access this endpoint.

The endpoint can be accessed at [this link](https://itk2yaesub.execute-api.us-east-1.amazonaws.com/prod/v1/usage).

Takes in two parameters:

```
userArn: str
monthYear: MM_YYYY
```

Outputs the usage and monthly limit:

```
{
    "current_usage": float,
    "monthly_limit": float
}
```
