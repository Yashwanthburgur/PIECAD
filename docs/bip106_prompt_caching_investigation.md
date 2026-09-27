# BIP 10.6 — Provider Prompt-Caching Support Investigation

## Executive Summary

**Current State**: The provider abstraction (OpenAI-compatible via `openai` SDK) does NOT currently support prompt caching. The active provider (NVIDIA Nemotron 3-Super via NVIDIA API gateway) does not expose cache-related token fields in the current integration.

**Recommendation**: Document as investigation result. The current architecture cannot safely implement caching without:

1. Provider-specific cache control parameters
2. Stable cacheable prefix identification
3. Cache metadata capture in telemetry

---

## Investigation Details

### 1. Current Provider Configuration

- **Provider**: NVIDIA Nemotron 3-Super (120B)
- **Endpoint**: `https://integrate.api.nvidia.com/v1` (NVIDIA API Gateway)
- **Interface**: OpenAI-compatible via `openai` Python SDK
- **Auth**: NVIDIA API Key (`DEEPSEEK_API_KEY` in .env)

### 2. Provider Abstraction Analysis (`providers/llm/provider.py`)

The `LLMProvider` class uses `openai.OpenAI` client with:

```python
kwargs = {
    "model": self.model,
    "messages": messages,
}
if tools:
    kwargs["tools"] = tools
    kwargs["tool_choice"] = "auto"
response = self.client.chat.completions.create(**kwargs)
```

**Missing for caching**:

- No `extra_headers` or `extra_query` for cache control
- No `prompt_cache_key` or similar parameter
- `last_usage` only captures: `prompt_tokens`, `completion_tokens`, `total_tokens`
- No capture of `prompt_tokens_details.cached_tokens` (OpenAI) or `cache_read_input_tokens` (Anthropic)

### 3. Prompt Caching Support by Major Providers

| Provider            | Cache Mechanism                         | API Parameters                                     | Response Fields                                                      |
| ------------------- | --------------------------------------- | -------------------------------------------------- | -------------------------------------------------------------------- |
| **OpenAI**          | Automatic prefix caching (1024+ tokens) | None (automatic)                                   | `usage.prompt_tokens_details.cached_tokens`                          |
| **Anthropic**       | Explicit cache control                  | `cache_control: {"type": "ephemeral"}` on messages | `usage.cache_creation_input_tokens`, `usage.cache_read_input_tokens` |
| **Google Gemini**   | Context caching                         | `cached_content` parameter                         | `usageMetadata.cachedContentTokenCount`                              |
| **NVIDIA Nemotron** | **Unknown/Not documented**              | Not exposed via gateway                            | Not exposed in current integration                                   |

### 4. Cacheable Context Analysis

In PieCAD's ReAct loop, the following context components are assembled per step:

```
[SYSTEM PROMPT] + [STATE] + [MEMORY] + [HISTORY] + [TOOL SCHEMAS] + [USER MESSAGE] + [SCRATCHPAD]
```

**Stability across steps**:

- System prompt: **Stable** (same across all steps in a turn)
- Tool schemas: **Variable** (filtered by router per step)
- State: **Variable** (changes after each tool execution)
- Memory/History: **Variable** (grows with conversation)
- User message: **Stable** (same per turn)
- Scratchpad: **Variable** (grows with tool calls)

**Conclusion**: No stable prefix of ≥1024 tokens exists that would benefit from OpenAI's automatic caching. The tool schemas (largest component) change every step due to router filtering.

### 5. Required Architecture Changes for Caching Support

If prompt caching were to be supported, the following minimal changes would be needed:

#### A. Provider Abstraction Extension

```python
# In providers/llm/provider.py
class LLMProvider:
    def __init__(self, ..., enable_prompt_caching: bool = False):
        self.enable_prompt_caching = enable_prompts_caching
        # Provider-specific cache config

    def generate_with_tools(self, messages, tools=None, cache_key: str = None):
        kwargs = {...}
        if self.enable_prompt_caching and cache_key:
            # Provider-specific: OpenAI doesn't need this, Anthropic needs cache_control
            pass
        # Capture cache metadata in last_usage
```

#### B. Cacheable Prefix Identification

```python
# In core/agent.py - identify stable prefix
def _get_cacheable_prefix(self, compiled_context):
    # System prompt + user message are stable per turn
    # Tool schemas vary per step - NOT cacheable
    return stable_messages
```

#### C. Telemetry Extension (BIP 10.2 compatible)

```python
# In core/context/telemetry.py - extend ContextTelemetry
@dataclass
class ContextTelemetry:
    # ... existing fields ...
    cached_input_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cache_available: bool = False
```

---

## Test Results

### Provider Capability Detection

```python
# Current provider (NVIDIA Nemotron) - NO cache metadata in response
provider = LLMProvider()
response = provider.generate_with_tools(...)
# provider.last_usage contains only: prompt_tokens, completion_tokens, total_tokens
# NO cached_tokens, cache_read_input_tokens, cache_creation_input_tokens
```

### Unsupported Provider Behavior

- Current behavior: Works correctly, no caching attempted
- No fabricated cache metrics
- Telemetry remains accurate

---

## Implementation Path (If Required Later)

If a future provider supports caching (e.g., switching to OpenAI gpt-4o or Anthropic):

1. **Minimal Provider Changes** (providers/llm/provider.py):
   - Add optional `cache_control` parameter to `generate_with_tools`
   - Capture cache-related usage fields in `last_usage`
   - Return cache metadata alongside normal usage

2. **Agent Integration** (core/agent.py):
   - Detect provider caching capability
   - For stable prefixes (system + user message), add cache control
   - Extend token telemetry to include cached tokens

3. **Telemetry** (core/context/telemetry.py):
   - Add optional cache fields to `ContextTelemetry`
   - Distinguish normal vs cached input tokens

---

## Conclusion

**No implementation performed**. The current provider (NVIDIA Nemotron via NVIDIA gateway) does not expose prompt caching functionality, and the ReAct loop's context does not have a stable cacheable prefix due to per-step tool schema filtering by the router.

The investigation is complete and documented. If the provider changes or caching becomes available, the minimal provider-boundary changes are documented above.

---

## Files Inspected

- `providers/llm/provider.py` - Provider abstraction
- `core/agent.py` - ReAct loop, context compilation, telemetry
- `core/context/compiler.py` - Context assembly, tool selection
- `core/context/telemetry.py` - Token telemetry structures
- `.env` - Provider configuration
