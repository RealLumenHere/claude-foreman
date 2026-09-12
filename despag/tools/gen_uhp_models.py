#!/usr/bin/env python3
"""wq#1805: generate despag/uhp/models.py from the UHP OpenAPI schema.

No third-party codegen (datamodel-codegen is not installed on Lumen and pip
is externally-managed) so this is a small, committed, in-repo generator
instead, per PHASE0_WIRING_SPEC.md B.1 row 10. It reads
`components.schemas` from the OpenAPI 3.1 YAML and emits one pydantic v2
BaseModel per named object schema (or a `Literal`/plain-type alias for a
non-object schema like `ResponseStatus`), plus a nested model for every
inline (unnamed) object schema it finds along the way. Schema wins on
shape; this generator does not add fields the schema doesn't have.

Exact invocation to reproduce despag/uhp/models.py:

    python3 despag/tools/gen_uhp_models.py \\
        /home/lumen/research/harnessrouter-ref/src/protocol/schema/uhp-2026-08-11.openapi.yaml \\
        despag/uhp/models.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

PRIMITIVE = {"string": "str", "integer": "int", "number": "float", "boolean": "bool"}


def pascal(name: str) -> str:
    return "".join(part.capitalize() for part in name.replace("-", "_").split("_"))


class Generator:
    def __init__(self, schemas: dict) -> None:
        self.schemas = schemas
        self.extra_classes: list[str] = []  # nested classes, emitted before main ones
        self.main_classes: list[str] = []
        self.aliases: list[str] = []
        self.class_names: list[str] = []  # for the final model_rebuild() loop

    def ref_name(self, ref: str) -> str:
        return ref.rsplit("/", 1)[-1]

    def resolve(self, schema: dict, schema_name: str, prop_name: str) -> str:
        if "$ref" in schema:
            return self.ref_name(schema["$ref"])

        if "oneOf" in schema:
            branches = [b for b in schema["oneOf"] if b.get("type") != "null"]
            is_nullable = len(branches) != len(schema["oneOf"])
            types = [self.resolve(b, schema_name, prop_name) for b in branches]
            inner = types[0] if len(types) == 1 else f"Union[{', '.join(types)}]"
            return f"Optional[{inner}]" if is_nullable else inner

        t = schema.get("type")
        if isinstance(t, list):
            non_null = [x for x in t if x != "null"]
            inner_schema = dict(schema, type=non_null[0] if len(non_null) == 1 else non_null)
            inner = self.resolve(inner_schema, schema_name, prop_name)
            return f"Optional[{inner}]" if "null" in t else inner

        if "const" in schema:
            return f'Literal[{schema["const"]!r}]'

        if "enum" in schema:
            return f"Literal[{', '.join(repr(v) for v in schema['enum'])}]"

        if t == "array":
            item_type = self.resolve(schema.get("items", {}), schema_name, prop_name + "_item")
            return f"List[{item_type}]"

        if t == "object" or "properties" in schema:
            props = schema.get("properties")
            add = schema.get("additionalProperties")
            if props:
                nested_name = f"{schema_name}{pascal(prop_name)}"
                self._emit_class(nested_name, schema, nested=True)
                return nested_name
            if isinstance(add, dict) and add.get("properties"):
                nested_name = f"{schema_name}{pascal(prop_name)}Item"
                self._emit_class(nested_name, add, nested=True)
                return f"Dict[str, {nested_name}]"
            if isinstance(add, dict):
                return f"Dict[str, {self.resolve(add, schema_name, prop_name + '_value')}]"
            return "Dict[str, Any]"

        return PRIMITIVE.get(t, "Any")

    def _emit_class(self, name: str, schema: dict, nested: bool) -> None:
        required = set(schema.get("required", []))
        lines = [f"class {name}(BaseModel):"]
        add = schema.get("additionalProperties")
        if add is True:
            lines.append('    model_config = ConfigDict(extra="allow")')
        elif add is False:
            lines.append('    model_config = ConfigDict(extra="forbid")')
        props = schema.get("properties", {})
        if not props:
            lines.append("    pass")
        for pname, pschema in props.items():
            pytype = self.resolve(pschema, name, pname)
            has_default = "default" in pschema
            is_const = "const" in pschema
            if is_const:
                lines.append(f"    {pname}: {pytype} = {pschema['const']!r}")
            elif pname in required and not pytype.startswith("Optional[") and not has_default:
                lines.append(f"    {pname}: {pytype}")
            elif has_default:
                default = pschema["default"]
                lines.append(f"    {pname}: {pytype} = {default!r}")
            else:
                base = pytype[len("Optional[") : -1] if pytype.startswith("Optional[") else pytype
                lines.append(f"    {pname}: Optional[{base}] = None")
        body = "\n".join(lines) + "\n"
        self.class_names.append(name)
        if nested:
            self.extra_classes.append(body)
        else:
            self.main_classes.append(body)

    def generate(self) -> str:
        for name, schema in self.schemas.items():
            if schema.get("type") == "object" or "properties" in schema:
                self._emit_class(name, schema, nested=False)
            elif "enum" in schema:
                self.aliases.append(f"{name} = Literal[{', '.join(repr(v) for v in schema['enum'])}]")
            else:
                self.aliases.append(f"{name} = {PRIMITIVE.get(schema.get('type'), 'Any')}")

        header = (
            '"""Generated from uhp-2026-08-11.openapi.yaml by despag/tools/gen_uhp_models.py.\n'
            "DO NOT EDIT BY HAND — re-run the generator (see its module docstring for the exact\n"
            'invocation) to reproduce this file after a schema change."""\n'
            "from __future__ import annotations\n\n"
            "from typing import Any, Dict, List, Literal, Optional, Union\n\n"
            "from pydantic import BaseModel, ConfigDict\n\n"
        )
        rebuilds = "\n".join(f"{n}.model_rebuild()" for n in self.class_names)
        parts = [header]
        parts.extend(self.extra_classes)
        parts.extend(self.main_classes)
        parts.extend(self.aliases)
        parts.append("\n# Resolve forward references created by inline nested schemas above.\n" + rebuilds + "\n")
        return "\n\n".join(p.rstrip("\n") for p in parts) + "\n"


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(2)
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    spec = yaml.safe_load(src.read_text())
    schemas = spec["components"]["schemas"]
    out = Generator(schemas).generate()
    dst.write_text(out)
    print(f"wrote {dst} ({len(schemas)} schemas)")


if __name__ == "__main__":
    main()
