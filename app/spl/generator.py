from app.spl.base import BaseSPLGenerator
from app.context.generation_context import GenerationContext
from app.generation.engine import TemplateGenerationEngine


class MockGenerator(BaseSPLGenerator):
    """SPL Generator using TemplateGenerationEngine to generate grounded queries from templates."""

    def __init__(self):
        self.engine = TemplateGenerationEngine()
        self.last_intent = None
        self.last_generator_confidence = 1.0

    def generate(self, context: GenerationContext) -> str:
        spl, intent, conf = self.engine.generate_spl(context)
        self.last_intent = intent
        self.last_generator_confidence = conf
        return spl
