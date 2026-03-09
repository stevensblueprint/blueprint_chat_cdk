# Document Q&A Agent API

The agent answers questions about documents stored in S3 and maintains multi-turn chat history per conversation.

---

## Base URL

Retrieved from the `AgentApiUrl` CloudFormation output after deploy:

```bash
aws cloudformation describe-stacks \
  --stack-name blueprint-chat-cdk \
  --query "Stacks[0].Outputs[?OutputKey=='AgentApiUrl'].OutputValue" \
  --output text
```

Format: `https://<api-id>.execute-api.<region>.amazonaws.com/prod/v1/agent`

---

## Endpoints

### POST /v1/agent

Send a prompt to the agent. The agent reads documents from S3, retrieves prior conversation turns from DynamoDB, and replies using Claude.

#### Request

```http
POST /v1/agent
Content-Type: application/json
```

| Field            | Type   | Required | Description                                                                                       |
| ---------------- | ------ | -------- | ------------------------------------------------------------------------------------------------- |
| `prompt`         | string | Yes      | The user's message or question.                                                                   |
| `conversationId` | string | No       | ID of an existing conversation. Omit on the first message — the agent will create and return one. |

**Example — first message:**

```json
{
  "prompt": "What documents are available?"
}
```

**Example — follow-up in the same conversation:**

```json
{
  "prompt": "Can you summarize the first one?",
  "conversationId": "a3f2c1d4-..."
}
```

#### Response

**200 OK**

```json
{
  "response": "There are 3 documents available: ...",
  "conversationId": "a3f2c1d4-..."
}
```

| Field            | Type   | Description                                              |
| ---------------- | ------ | -------------------------------------------------------- |
| `response`       | string | The agent's reply.                                       |
| `conversationId` | string | Pass this back on subsequent calls to continue the chat. |

**400 Bad Request** — `prompt` was empty or missing:

```json
{ "error": "prompt is required" }
```

**500 Internal Server Error** — upstream failure:

```json
{ "error": "<error detail>" }
```

---

## How It Works

1. **History** — The last 10 turns for the given `conversationId` are loaded from the `ChatHistory` DynamoDB table and included in the request to Claude as prior messages.
2. **Documents** — Up to 5 objects from the `blueprint-chat-documents-<account>` S3 bucket are read (up to 3 000 characters each) and injected into the system prompt as context.
3. **Model** — Claude 3 Haiku (`anthropic.claude-3-haiku-20240307-v1:0`) via Bedrock `converse`.
4. **Persistence** — The user turn and assistant reply are written back to DynamoDB so the next call in the same conversation has full context.

---

## CORS

All responses include `Access-Control-Allow-Origin: *`. Preflight `OPTIONS` requests are handled automatically and return `200`.

---

## Usage Examples

### curl

```bash
# First message
curl -X POST <AgentApiUrl> \
  -H "Content-Type: application/json" \
  -d '{"prompt": "What documents are available?"}'

# Follow-up
curl -X POST <AgentApiUrl> \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Summarize the first one", "conversationId": "a3f2c1d4-..."}'
```

### JavaScript (fetch)

```js
const BASE_URL = "<AgentApiUrl>";

async function ask(prompt, conversationId) {
  const res = await fetch(BASE_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, conversationId }),
  });
  return res.json(); // { response, conversationId }
}

// First turn
const { response, conversationId } = await ask("What documents are available?");

// Follow-up in the same conversation
const reply = await ask("Can you give more detail?", conversationId);
```

### TypeScript

```ts
interface AgentRequest {
  prompt: string;
  conversationId?: string;
}

interface AgentResponse {
  response: string;
  conversationId: string;
}

async function askAgent(req: AgentRequest): Promise<AgentResponse> {
  const res = await fetch("<AgentApiUrl>", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
```
