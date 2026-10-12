"""wq#3757 — research/fleet_hardware.json stays parseable, complete, and honest.

The manifest is the research-pipeline quality gate's filter input: a tool that
doesn't support this fleet's hardware gets silently dropped. A manifest that
drifts (missing field, device class dropped in an edit, a guessed spec replacing
a recorded-null) silently widens or narrows that filter, so the schema the file
declares for itself is enforced here against the committed file — a real
artifact, so every probe can fail.
"""
import json
import os
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HARNESS_DIR)
# conftest's autouse _fresh_mask_state fixture does `import fworker`, which resolves
# from HARNESS_DIR (same arrangement as every other module in this dir).
sys.path.insert(0, HARNESS_DIR)
MANIFEST = os.path.join(REPO, 'research', 'fleet_hardware.json')


@pytest.fixture(scope='module')
def manifest():
    with open(MANIFEST) as f:
        return json.load(f)


def test_declared_node_schema_is_enforced(manifest):
    """Every node carries every field _meta.schema.required_node_fields declares."""
    required = manifest['_meta']['schema']['required_node_fields']
    assert required, 'manifest stopped declaring required node fields'
    for name, node in manifest['nodes'].items():
        missing = [k for k in required if k not in node]
        assert not missing, f'node {name} missing fields: {missing}'


def test_ticket_named_hardware_all_present(manifest):
    """wq#3757 names eight device sets; losing any one re-opens the ticket's gap.
    Scan only the inventory sections — fleet_constraints/_meta also mention these
    names in prose, which would mask a dropped entry."""
    blob = json.dumps({'nodes': manifest['nodes'],
                       'device_classes': manifest['device_classes']})
    for marker in (
        'RK3399',              # Station P1 nodes (khnum/neith/ptah/sekhmet)
        'Mac Pro 6,1',         # trashcan pool
        'BC-250',              # hapi blades
        'Cyan Skillfish',
        'Precision Tower 5810',  # lumen
        'M70s',                # sentinel / m70s-2
        'M80s',                # m80s
        'MT8167',              # miko2/miko3/winnovo-m8
        'Ingenic T40',         # miko-mini
        'S905X3',              # spectrio-tf1
    ):
        assert marker in blob, f'manifest lost ticket-named hardware: {marker}'


def test_rk3399_nodes_are_fleet_nodes(manifest):
    """The four Station P1 boxes are SSH fleet nodes, not RE device classes."""
    for name in ('khnum', 'neith', 'ptah', 'sekhmet'):
        assert name in manifest['nodes'], f'{name} missing from nodes'
        assert manifest['nodes'][name]['arch'] == 'aarch64'


def test_filter_constraints_present_and_typed(manifest):
    """The gate's hard constraints: booleans stay booleans (a string 'false' is truthy
    to a naive consumer and would invert the filter)."""
    fc = manifest['fleet_constraints']
    for key in ('nvidia_cuda', 'rocm', 'apple_silicon', 'opencl', 'avx512'):
        assert fc[key] is False, f'fleet_constraints.{key} must be literal false'
    assert fc['max_single_node_ram_gb'] == 64
    assert 'x86_64' in fc['architectures_present'][0]


def test_unknowns_are_null_not_guessed(manifest):
    """Doctrine: never guess a spec. Unprovisioned boxes keep null fields rather
    than invented values — m70s-2 has no recorded CPU/RAM anywhere."""
    m70s2 = manifest['nodes']['m70s-2']
    assert m70s2['cpu'] is None
    assert m70s2['ram_gb'] is None
