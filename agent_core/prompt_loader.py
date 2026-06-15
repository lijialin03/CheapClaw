# agent_core/prompt_loader.py
import re
from functools import lru_cache
from pathlib import Path


PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
PLACEHOLDER_PATTERN = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")


@lru_cache(maxsize=None)
def load_prompt_template(name: str) -> str:
    path = (PROMPT_DIR / name).resolve()
    if not path.is_file() or PROMPT_DIR not in path.parents:
        raise FileNotFoundError(f"Prompt template not found: {name}")
    return path.read_text(encoding="utf-8")


def render_prompt(name: str, **variables: object) -> str:
    template = load_prompt_template(name)

    def replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in variables:
            raise KeyError(f"Missing prompt variable: {key}")
        return str(variables[key])

    return PLACEHOLDER_PATTERN.sub(replace, template)
