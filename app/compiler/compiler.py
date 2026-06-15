import time
from typing import List
from app.domain.enums import CompilationStatus, StepType
from app.domain.models import Runbook, CompilationResult, CompiledStep
from app.diagnostics.models import CompilationWarning, CompilationError
from app.tracing.models import CompilationTrace
from app.context.generation_context import GenerationContext
from app.spl.base import BaseSPLGenerator, BaseSPLOptimizer, BaseSPLExplainer, BaseSPLValidator
from app.schema.base import BaseSchemaProvider
from app.grounding.resolver import SchemaGroundingEngine
from app.grounding.confidence import ConfidenceCalculator
from app.observability.request_context import get_request_id, get_correlation_id


class RunbookCompiler:
    """
    Compiles a Runbook domain model into a structured CompilationResult using
    the provided generator, optimizer, explainer, and validator adapters,
    enriched with schema provider metadata, grounding analysis, and confidence tracking.
    """

    def __init__(
        self,
        generator: BaseSPLGenerator,
        optimizer: BaseSPLOptimizer,
        explainer: BaseSPLExplainer,
        validator: BaseSPLValidator,
        schema_provider: BaseSchemaProvider
    ):
        self.generator = generator
        self.optimizer = optimizer
        self.explainer = explainer
        self.validator = validator
        self.schema_provider = schema_provider
        self.grounding_engine = SchemaGroundingEngine()

    def compile(self, runbook: Runbook) -> CompilationResult:
        """
        Executes compilation pipeline sequentially for each step in the Runbook,
        running grounding and overall confidence calculation for trace validation.
        """
        compiled_steps: List[CompiledStep] = []
        errors: List[CompilationError] = []
        warnings: List[CompilationWarning] = []
        traces: List[CompilationTrace] = []

        for step in runbook.steps:
            start_time = time.perf_counter()
            step_id = step.step_id
            
            # Step-specific diagnostics lists
            step_errors: List[CompilationError] = []
            step_warnings: List[CompilationWarning] = []
            
            # Track validation outcomes
            validation_results = {"raw_valid": False, "optimized_valid": False}

            # Check for warnings based on metadata
            if step.step_type == StepType.MANUAL:
                w = CompilationWarning(
                    code="WRN_MANUAL_STEP",
                    message=f"Step '{step_id}' is marked as MANUAL. Compilation is structural only.",
                    step_id=step_id
                )
                step_warnings.append(w)
                warnings.append(w)
            if step.confidence < 0.8:
                w = CompilationWarning(
                    code="WRN_LOW_CONFIDENCE",
                    message=f"Step '{step_id}' was decomposed with low confidence ({step.confidence}).",
                    step_id=step_id
                )
                step_warnings.append(w)
                warnings.append(w)

            # Retrieve schema fields via Schema Provider
            schema_fields = []
            if step.data_source:
                schema_fields = self.schema_provider.get_fields(step.data_source)

            # Perform Schema Grounding Checks
            grounding_res = self.grounding_engine.ground(step.description, schema_fields)
            
            # Collect any grounding warnings (e.g. missing fields)
            for gw_msg in grounding_res.warnings:
                w = CompilationWarning(
                    code="WRN_SCHEMA_GROUNDING",
                    message=gw_msg,
                    step_id=step_id
                )
                step_warnings.append(w)
                warnings.append(w)

            # Construct GenerationContext
            context = GenerationContext(
                step=step,
                schema_fields=schema_fields,
                data_source=step.data_source,
                constraints={"time_window": step.time_window} if step.time_window else {},
                metadata={"grounding": grounding_res.model_dump()}
            )

            raw_spl = None
            optimized_spl = None
            explanation = None
            step_status = CompilationStatus.FAILED
            generator_conf = 1.0  # Default mock generator confidence

            # 1. Generate Raw SPL
            try:
                raw_spl = self.generator.generate(context)
                
                # Fetch explainability metadata if populated by generator
                generator_conf = getattr(self.generator, "last_generator_confidence", 1.0)
                selected_template = getattr(self.generator, "last_intent", "GENERIC")
                grounded_fields = grounding_res.resolved_fields

                # 2. Validate Raw SPL
                if not self.validator.validate(raw_spl, context):
                    e = CompilationError(
                        code="ERR_VAL_RAW",
                        message=f"Generated raw SPL '{raw_spl}' failed validation.",
                        step_id=step_id
                    )
                    step_errors.append(e)
                    errors.append(e)
                else:
                    validation_results["raw_valid"] = True
                    
                    # 3. Optimize SPL
                    try:
                        optimized_spl = self.optimizer.optimize(raw_spl, context)
                        
                        # 4. Validate Optimized SPL
                        if not self.validator.validate(optimized_spl, context):
                            e = CompilationError(
                                code="ERR_VAL_OPT",
                                message=f"Optimized SPL '{optimized_spl}' failed validation.",
                                step_id=step_id
                            )
                            step_errors.append(e)
                            errors.append(e)
                        else:
                            validation_results["optimized_valid"] = True
                            
                            # 5. Explain SPL
                            try:
                                explanation = self.explainer.explain(optimized_spl, context)
                                step_status = CompilationStatus.SUCCESS
                            except Exception as ex:
                                w = CompilationWarning(
                                    code="WRN_EXP_FAIL",
                                    message=f"Failed to explain query: {str(ex)}",
                                    step_id=step_id
                                )
                                step_warnings.append(w)
                                warnings.append(w)
                                explanation = "Explanation unavailable."
                                step_status = CompilationStatus.SUCCESS
                    except Exception as ex:
                        e = CompilationError(
                            code="ERR_OPT_FAIL",
                            message=f"Failed to optimize SPL: {str(ex)}",
                            step_id=step_id
                        )
                        step_errors.append(e)
                        errors.append(e)
            except Exception as ex:
                e = CompilationError(
                    code="ERR_GEN_FAIL",
                    message=f"Failed to generate raw SPL: {str(ex)}",
                    step_id=step_id
                )
                step_errors.append(e)
                errors.append(e)

            # Calculate overall confidence
            if step_status == CompilationStatus.SUCCESS:
                overall_conf = ConfidenceCalculator.calculate_overall(
                    parser_conf=step.confidence,
                    grounding_conf=grounding_res.confidence,
                    generator_conf=generator_conf
                )
            else:
                overall_conf = 0.0

            # RecordCompiledStep
            compiled_steps.append(CompiledStep(
                step_id=step_id,
                description=step.description,
                raw_spl=raw_spl,
                compiled_spl=optimized_spl,
                explanation=explanation,
                status=step_status,
                confidence=overall_conf
            ))

            # Compute execution duration
            duration_ms = (time.perf_counter() - start_time) * 1000

            # Record Compilation Trace for step
            traces.append(CompilationTrace(
                step_id=step_id,
                generated_spl=raw_spl,
                optimized_spl=optimized_spl,
                validation_results=validation_results,
                execution_duration_ms=duration_ms,
                errors=[e.message for e in step_errors],
                warnings=[w.message for w in step_warnings],
                grounding_result=grounding_res,
                overall_confidence=overall_conf,
                selected_template=selected_template if raw_spl else None,
                grounded_fields=grounded_fields if raw_spl else [],
                request_id=get_request_id(),
                correlation_id=get_correlation_id()
            ))

        # Determine overall runbook compilation status
        success_count = sum(1 for s in compiled_steps if s.status == CompilationStatus.SUCCESS)
        total_count = len(compiled_steps)

        if success_count == total_count and total_count > 0:
            overall_status = CompilationStatus.SUCCESS
        elif success_count > 0:
            overall_status = CompilationStatus.PARTIAL
        else:
            overall_status = CompilationStatus.FAILED

        return CompilationResult(
            runbook_name=runbook.name,
            steps=compiled_steps,
            status=overall_status,
            errors=errors,
            warnings=warnings,
            traces=traces
        )
