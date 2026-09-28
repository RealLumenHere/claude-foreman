"""Mentions of mistral-small in a docstring are history, not a call.
payload = {"model": "mistral-small"}
"""
# a comment naming "mistral-small" is not a call either
MODE = "local"
payload = {"model": "deepseek-v4-flash", "messages": []}
judge = call(model="hathor-llama8b")
ollama = {"model": "qwen3:8b"}
routed = {"model": "openai/gpt-4o-mini"}
JUDGE_MODEL = "cerebras-llama"
cli = "claude --model claude-opus-5"
PROVIDER_TO_MODEL = {"mistral": "deepseek-v4-flash",  # wq#2903: mistral-small left the gateway
}
