claude = "anthropic/claude-3-7-sonnet-latest"
claudeopus4 = "anthropic:claude-opus-4-0"
claudeopus41 = "anthropic:claude-opus-4-1"
claude45 = "anthropic:claude-sonnet-4-5"
claude37 = "anthropic:claude-3-7-sonnet-latest"
claude4 = "anthropic/claude-sonnet-4-0"
gpt51 = "openai:gpt-5.1"
gpt52 = "openai:gpt-5.2"
gpt5 = "openai:gpt-5"
gpt5_mini = "openai:gpt-5-mini"
gpt4_1 = "gpt-4.1"
gpt4_1_mini = "gpt-4.1-mini"
gpt4_1_nano = "gpt-4.1-nano"
gpt5_nano = "openai:gpt-5-nano"
ollama = "ollama/qwen3:32b"
vllm_qwen3 = "Qwen/Qwen3-235B-A22B-Thinking-2507-FP8"

model_costs = {
    gpt51: {
        "input": 1.25,
        "cached_input": 0.125,
        "output": 10,
        "web_search": 0.01,
    },
    gpt52: {
        "input": 1.75,
        "cached_input": 0.175,
        "output": 14,
        "web_search": 0.01,
    },
    gpt5: {
        "input": 1.25,
        "cached_input": 0.125,
        "output": 10,
        "web_search": 0.01,
    },
    gpt5_mini: {
        "input": 0.25,
        "cached_input": 0.025,
        "output": 2,
        "web_search": 0.01,
    },
    gpt5_nano: {
        "input": 0.05,
        "cached_input": 0.005,
        "output": 0.4,
        "web_search": 0.01,
    },
    gpt4_1: {
        "input": 2,
        "cached_input": 0.5,
        "output": 8,
        "web_search": 0.01,
    },
    gpt4_1_mini: {
        "input": 0.4,
        "cached_input": 0.1,
        "output": 1.6,
        "web_search": 0.01,
    },
    gpt4_1_nano: {
        "input": 0.1,
        "cached_input": 0.025,
        "output": 0.4,
        "web_search": 0.01,
    },
    claude45: {
        "input": 3,
        "cached_input": 0.3,
        "output": 15,
        "web_search": 0.01,
    },
    claude37: {
        "input": 3,
        "cached_input": 0.3,
        "output": 15,
        "web_search": 0.01,
    },
    claudeopus4: {
        "input": 15,
        "cached_input": 1.5,
        "output": 75,
        "web_search": 0.01,
    },
    claudeopus41: {
        "input": 15,
        "cached_input": 1.5,
        "output": 75,
        "web_search": 0.01,
    },
}
