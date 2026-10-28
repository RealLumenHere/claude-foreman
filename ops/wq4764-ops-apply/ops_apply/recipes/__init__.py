"""Recipe registry. A recipe is a root-owned file in a fixed catalog
(design §3.3) — this module maps (id, version) to the recipe's python
module, which is the only indirection the gate/node servers need. Adding a
recipe means adding one entry here plus a catalog.json row; it never means
the agent supplying a path, a template, or anything executed."""
from . import cloudflared_ingress_upsert as _cloudflared_ingress_upsert

REGISTRY = {
    ("cloudflared.ingress.upsert", 1): _cloudflared_ingress_upsert,
}


def get_recipe(recipe_id, version):
    return REGISTRY.get((recipe_id, int(version)))
