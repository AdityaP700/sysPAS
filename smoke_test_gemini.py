import os, sys
sys.path.insert(0, '.')

os.environ['RUNBOOKMIND_GEMINI_API_KEY'] = 'dummy-test-key'

from app.splunk.adapters.gemini_spl_service import (
    _make_cache_key,
    build_local_explanation,
    build_local_optimization_notes,
)
from app.context.generation_context import GenerationContext
from app.domain.models import RunbookStep
from app.domain.enums import StepType

step = RunbookStep(
    step_id='1',
    description='Find all failed logins in the last 15 minutes',
    step_type=StepType.INVESTIGATION,
    data_source='auth',
    time_window='15m',
)
ctx = GenerationContext(
    step=step,
    schema_fields=['src_ip', 'user', 'action'],
    data_source='auth',
    constraints={'time_window': '15m'},
)

key = _make_cache_key(ctx)
print(f'Cache key OK: {key[:16]}...')

fake_spl = "index=auth earliest=-15m | stats count by src_ip, user | where count > 5"
expl = build_local_explanation(fake_spl, ctx)
print(f'Explanation:\n{expl}')

notes = build_local_optimization_notes(fake_spl)
print(f'Optimization notes:\n{notes}')

# Verify adapter imports
from app.splunk.adapters.mcp_generator import SplunkMCPGenerator
from app.splunk.adapters.mcp_explainer import SplunkMCPExplainer
from app.splunk.adapters.mcp_optimizer import SplunkMCPOptimizer
print('\nAdapter imports OK')

# Verify settings fields
from app.config.settings import settings
assert hasattr(settings, 'gemini_api_key'), 'gemini_api_key missing from settings'
assert hasattr(settings, 'gemini_model'), 'gemini_model missing'
assert hasattr(settings, 'gemini_rpm_cap'), 'gemini_rpm_cap missing'
assert hasattr(settings, 'gemini_cache_ttl'), 'gemini_cache_ttl missing'
print(f'Settings OK: model={settings.gemini_model}, rpm_cap={settings.gemini_rpm_cap}, cache_ttl={settings.gemini_cache_ttl}')

print('\nAll checks passed.')

