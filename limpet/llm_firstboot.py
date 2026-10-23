"""wq#4512 -- LIMPET ISO first-boot LLM provider wizard.

The installed image carries no model decision (`docs/deployment-spec.md:160-161`:
"Limpet-specific choices (LLM, update policy) are written as settings defaults at
ISO build time and confirmed on first boot"). This module is that confirmation
step: a short interactive prompt run once on first boot that asks which LLM
provider the box should use, then writes `/etc/limpet/llm.toml` so
`limpet-agent.service` (and later `limpet-config`, `docs/settings-spec.md:159`)
have a config file to read.

Provider choices follow `docs/settings-spec.md:159-167`'s `llm.provider` enum,
narrowed to the three the ticket actually offers an operator at first boot
(wq#4512: "cloud key, local Ollama, none") -- `litellm-external` is a
post-install `limpet-config` choice, not a first-boot one, so it is left out
here. "none" is not in the settings-spec enum; it is the explicit opt-out that
`docs/deployment-spec.md:314-320`'s graceful-degradation requirement describes
("a user with no model and no network still has a working RE appliance") --
recorded as `llm.provider = "none"` so `limpet-agent.service` can tell "no
provider configured yet" apart from "operator chose no AI assistance".
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Optional

LLM_TOML_PATH = Path("/etc/limpet/llm.toml")

PROVIDERS = ("cloud-api", "ollama-direct", "none")

DEFAULT_GATEWAY_URL = "http://127.0.0.1:4000"
DEFAULT_OLLAMA_RESIDENT_MODEL = "qwen2.5-coder:7b"


class WizardError(ValueError):
    pass


def prompt_answers(
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> dict:
    """Ask the three first-boot questions and return a validated answers dict.

    Kept separate from `render_toml` / `write_llm_toml` so tests can drive the
    menu with a canned `input_fn` instead of a real TTY.
    """
    print_fn("Limpet first-boot setup: choose an LLM provider.")
    print_fn("  1) Cloud API key (e.g. Anthropic, OpenAI)")
    print_fn("  2) Local Ollama (runs on this box, no network needed)")
    print_fn("  3) None (run Limpet without AI assistance for now)")
    choice = input_fn("Choice [1-3]: ").strip()
    if choice == "1":
        key_file = input_fn("Path to the 0600 API key file: ").strip()
        return validate_answers({"provider": "cloud-api", "api_key_file": key_file})
    if choice == "2":
        model = input_fn(
            f"Ollama model to use [{DEFAULT_OLLAMA_RESIDENT_MODEL}]: "
        ).strip()
        return validate_answers(
            {"provider": "ollama-direct", "resident_model": model or DEFAULT_OLLAMA_RESIDENT_MODEL}
        )
    if choice == "3":
        return validate_answers({"provider": "none"})
    raise WizardError(f"unrecognized choice {choice!r}, expected 1, 2, or 3")


def validate_answers(answers: dict) -> dict:
    provider = answers.get("provider")
    if provider not in PROVIDERS:
        raise WizardError(f"llm.provider must be one of {PROVIDERS}, got {provider!r}")
    if provider == "cloud-api" and not answers.get("api_key_file"):
        raise WizardError("cloud-api provider requires api_key_file")
    return answers


def render_toml(answers: dict) -> str:
    """Render the validated answers as the `[llm]` table `docs/settings-spec.md:141-167` specifies.

    Hand-rolled rather than a TOML-writer dependency: the shape is one flat
    table of strings, well inside what `str`-formatting can do correctly, and
    the repo has no TOML *writer* anywhere today (every `import tomllib` in
    `limpet/` only reads).
    """
    answers = validate_answers(answers)
    lines = ["[llm]", f'provider = "{answers["provider"]}"']
    if answers["provider"] == "cloud-api":
        lines.append(f'gateway_url = "{DEFAULT_GATEWAY_URL}"')
        lines.append(f'api_key_file = "{answers["api_key_file"]}"')
    elif answers["provider"] == "ollama-direct":
        lines.append(f'resident_model = "{answers["resident_model"]}"')
    lines.append("")
    return "\n".join(lines)


def write_llm_toml(answers: dict, path: Path = LLM_TOML_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_toml(answers))
    path.chmod(0o644)
    return path


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path", type=Path, default=LLM_TOML_PATH, help="where to write llm.toml"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing llm.toml instead of skipping the wizard",
    )
    args = parser.parse_args(argv)

    if args.path.exists() and not args.force:
        print(f"{args.path} already exists; skipping first-boot LLM wizard.")
        return 0

    try:
        answers = prompt_answers()
    except WizardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    written = write_llm_toml(answers, args.path)
    print(f"wrote {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
