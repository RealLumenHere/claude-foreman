# Verbatim pre-wq#2903 caller lines (2026-09-28), one per line so tests can pin line numbers.
PROVIDER_TO_MODEL = {"cerebras": "cerebras-oss-120b",
    "mistral":    "mistral-small",
}
MODELS = os.environ.get(
    "AUDIT_MODELS", "cerebras-llama,cerebras-oss-120b,deepseek-v4-flash,"
    "deepseek-v4-pro,mistral-small")
CLOUD  = ["cerebras-llama", "cerebras-oss-120b", "deepseek-v4-flash",
          "deepseek-v4-pro", "mistral-small"]
payload = {"model": "wq2821-judge-llama8b", "messages": []}
