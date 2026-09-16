# Models and providers

Connect your preferred model provider, then choose a model for each conversation.
You can save several connections—for example, a personal API account and a local
model server—and switch between them in TUI, Desktop, or Web.

## Configure a connection

To add a connection in Desktop/Web, open **Settings → AI providers** and choose
**Add provider**. Fill in your provider and API key, then click **Save and check**.

From the terminal, this example adds OpenRouter under the name `my-openrouter`
and lists its models:

```console
deepcode provider set my-openrouter --template openrouter --api-key
deepcode provider models my-openrouter --refresh
deepcode provider list
```

Enter your API key when prompted; it will stay hidden as you type. If you keep
the key in an environment variable, use `--api-key-env NAME` instead, replacing
`NAME` with the variable's name. Your saved connections are available across
projects on this computer.

Replace `MODEL_ID` with an exact ID in this connection's catalog:

```console
deepcode provider test my-openrouter --model MODEL_ID
```

This sends a short request to check that the model responds. Desktop/Web offer
the same check through **Save and verify model**.

When trying a new model server, you can also check streaming and tool calls:

```console
deepcode provider test my-openrouter --model MODEL_ID --agent
```

This asks the model to use a small local test tool; it does not run shell commands
or edit your files. Read the result for each check. If one is skipped, that
capability has not been tested. These checks use your provider's API quota.

## Select a model, then switch future turns

Use `/model` to open the TUI picker or specify both connection and model:

```text
/model my-openrouter MODEL_ID
```

At launch, use `deepcode --connection my-openrouter --model MODEL_ID` with your
workspace/trust options. Desktop/Web use the composer model picker. Configuring
or testing a connection does not select it for every Session.

You can switch models and keep the conversation. The new selection applies to
messages you send afterward; work already running or queued keeps the model it
started with.

## Explicit protocols and local services

If you use a local server or custom gateway, set its address and API protocol.
Choose `openai_chat`, `openai_responses`, or `anthropic_messages` to match the
API your server provides. For standard providers, the template's `auto` setting
usually handles this for you.

When you explicitly select `openai_responses`, your server must support the
Responses API; DeepCode will keep using that protocol if a request fails.

For a local OpenAI Chat-compatible service without authentication, replace
`LOCAL_MODEL_ID` and the example endpoint with your deployment's values:

```console
deepcode provider set local-vllm --template vllm --api-base http://127.0.0.1:8000/v1 --protocol openai_chat --auth none --catalog manual --model LOCAL_MODEL_ID
deepcode provider test local-vllm --model LOCAL_MODEL_ID --agent
```

Use `--auth none` only when that endpoint does not require authentication. For
an authenticated gateway, use `--auth api_key` with `--api-key` or
`--api-key-env`. For additional gateway settings or OpenRouter browser login,
see the [Provider configuration reference](../PROVIDER_CONNECTIONS.md).

### Amazon Bedrock

Bedrock's OpenAI-compatible endpoint is region-specific. Supply its
`/openai/v1` base URL and expose an Amazon Bedrock API key through the standard
`AWS_BEARER_TOKEN_BEDROCK` environment variable:

```console
deepcode provider set work-bedrock --template bedrock \
  --api-base https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1 \
  --api-key-env AWS_BEARER_TOKEN_BEDROCK
deepcode provider models work-bedrock --refresh
deepcode provider test work-bedrock --model us.anthropic.claude-sonnet-4-6
```

Replace `us-east-1` with the Region that serves your account and choose a model
ID returned by the refresh command. Bedrock may return an in-Region ID such as
`anthropic.claude-sonnet-4-6`, a geography-scoped ID such as
`us.anthropic.claude-sonnet-4-6` or `eu.anthropic.claude-sonnet-4-6`, or a
`global.` ID depending on model and Region.

This first integration sends the configured value as a bearer token through
Bedrock's OpenAI-compatible Chat Completions API. It does **not** implement AWS
SigV4 signing, IAM role/profile discovery, SSO, or the native Converse API.

## Declared models and capacities

If your server's model is missing from the list, add it manually. You can also
set its context window and output limit to match your deployment.

Open `~/.deepcode/deepcode_config.json` and add the following connection under
`providers.profiles`, keeping your other settings:

```json
{
  "local-vllm": {
    "template": "vllm",
    "apiBase": "http://127.0.0.1:8000/v1",
    "protocol": "openai_chat",
    "auth": "none",
    "modelCatalog": "manual",
    "manualModels": [
      {
        "id": "LOCAL_MODEL_ID",
        "contextWindow": 65536,
        "maxOutputTokens": 4096
      }
    ]
  }
}
```

Replace `LOCAL_MODEL_ID` with your server's model ID and the example limits
with those supported by your deployment. Use these settings to tell DeepCode
how much context and output the model can handle.

## Reasoning effort

For models that offer reasoning levels, use `/effort` to choose how much thinking
to request. For example, if your model supports `high`:

```text
/effort high
```

Use a level offered for the selected model; `/effort auto` leaves the choice to
the provider, and `/effort off` disables extended thinking where supported.
Choose from the levels shown for your model. Use `Ctrl+O` separately when you
want to change how much activity the TUI displays.

## Context window cap

To keep conversations within a smaller context budget, set a cap such as `64k`.
Use `auto` to return to the model's known window:

```text
/context 64k
/context auto
```

The first narrows the Session's context budget; the second follows the selected
model's known window. The cap must leave room for the generation limit and cannot
expand the model's declared/catalog window. Desktop/Web expose the same setting
in the model picker. A smaller budget can trigger compaction sooner.

## Usage and troubleshooting

After a response, the footer shows elapsed time and token usage when your
provider reports it. Check your provider's billing page for charges. If a model
fails to connect or use tools, follow [Troubleshooting](troubleshooting.md).
