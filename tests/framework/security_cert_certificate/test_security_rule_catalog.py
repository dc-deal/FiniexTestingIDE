"""
FiniexTestingIDE - Security Rule Catalog Completeness (#358)

The catalog says how the certificate stands behind each rule, and a reader trusts it for that. So
it is held complete in both directions: every rule of the enum is declared, every live rule names
a check the analyzer computes and a stage whose profile holds the phase it names, every offline
rule names a test that exists, and every margin rule names the issue that carries it.
"""

import ast
import json
from pathlib import Path

import pytest

from python.framework.acceptance_studies.security_cert_stages import (
    SECURITY_STAGES,
    stage_profile_path,
)
from python.framework.reporting.certificates.security_cert_certificate import (
    SecurityCertCertificate,
)
from python.framework.reporting.certificates.security_cert_rule_catalog import (
    SECURITY_RULE_CATALOG,
)
from python.framework.types.acceptance_study_types.security_cert_types import (
    RuleCertification,
    SecurityRule,
)

_LIVE = [(rule, entry) for rule, entry in SECURITY_RULE_CATALOG.items()
         if entry.certification == RuleCertification.LIVE]
_OFFLINE = [(rule, entry) for rule, entry in SECURITY_RULE_CATALOG.items()
            if entry.certification == RuleCertification.OFFLINE]


def _phase_ids(stage_id) -> set:
    """The phase ids a stage's real profile declares."""
    stage = next(s for s in SECURITY_STAGES if s.stage_id == stage_id)
    profile = json.loads(stage_profile_path(stage).read_text(encoding='utf-8'))
    return {p['phase_id'] for p in profile['strategy_config']['decision_logic_config']['phase_sequence']}


def _node_exists(node_id: str) -> bool:
    """Whether a pytest node id names a file, a class and a test that exist."""
    path, *names = node_id.split('::')
    if not Path(path).exists():
        return False
    scope = ast.parse(Path(path).read_text(encoding='utf-8')).body
    for name in names:
        found = next((n for n in scope if isinstance(n, (ast.ClassDef, ast.FunctionDef))
                      and n.name == name), None)
        if found is None:
            return False
        scope = found.body if isinstance(found, ast.ClassDef) else []
    return True


def test_every_rule_is_declared():
    assert set(SECURITY_RULE_CATALOG) == set(SecurityRule)


@pytest.mark.parametrize('rule, entry', _LIVE, ids=lambda x: getattr(x, 'value', ''))
def test_a_live_rule_names_its_stage_and_a_check_the_analyzer_computes(
        rule, entry, passing_study, build):
    assert entry.stage is not None, f'{rule.value} is live but names no stage'
    checks = SecurityCertCertificate.analyze(build(passing_study))['checks']
    assert entry.evidence in checks, f'{rule.value}: the analyzer computes no `{entry.evidence}`'
    if entry.evidence.startswith('phase:'):
        phase_id = entry.evidence.split(':', 1)[1]
        assert phase_id in _phase_ids(entry.stage), (
            f'{rule.value}: stage {entry.stage.value} has no phase `{phase_id}`')


@pytest.mark.parametrize('rule, entry', _OFFLINE, ids=lambda x: getattr(x, 'value', ''))
def test_an_offline_rule_names_a_test_that_exists(rule, entry):
    assert _node_exists(entry.evidence), f'{rule.value}: no test `{entry.evidence}`'


def test_a_margin_rule_names_the_issue_that_carries_it():
    margin = [entry for entry in SECURITY_RULE_CATALOG.values()
              if entry.certification == RuleCertification.MARGIN]
    assert margin and all(entry.evidence == '#209' for entry in margin)
