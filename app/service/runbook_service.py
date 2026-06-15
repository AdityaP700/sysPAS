import time
import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional
from app.parser.markdown_parser import MarkdownParser
from app.parser.text_parser import TextParser
from app.validation.runbook_validator import RunbookValidator
from app.compiler.compiler import RunbookCompiler
from app.agent.compiler import AgentSkillCompiler
from app.package.manifest import AgentSkillManifest
from app.package.bundle import SkillBundle
from app.api.schemas import CompileRunbookResponse
from app.domain.enums import CompilationStatus
from app.config.settings import settings
from app.storage.sqlite import SQLiteRepository
from app.storage.bundle_store import BundleStore
from app.storage.compilation_store import CompilationStore
from app.storage.trace_store import TraceStore
from app.storage.models import CompilationRecord


class RunbookService:
    """Orchestrates the entire RunbookMind compilation pipeline and manages persistent storage integration."""

    def __init__(
        self,
        repo: Optional[SQLiteRepository] = None,
        bundle_store: Optional[BundleStore] = None,
        compilation_store: Optional[CompilationStore] = None,
        trace_store: Optional[TraceStore] = None,
        # Optional DI overrides — defaults to mock implementations when not supplied
        generator: Any = None,
        optimizer: Any = None,
        explainer: Any = None,
        validator: Any = None,
        schema_provider: Any = None,
    ):
        if generator is None:
            from app.spl.generator import MockGenerator
            generator = MockGenerator()
        if optimizer is None:
            from app.spl.optimizer import MockOptimizer
            optimizer = MockOptimizer()
        if explainer is None:
            from app.spl.explainer import MockExplainer
            explainer = MockExplainer()
        if validator is None:
            from app.spl.validator import MockValidator
            validator = MockValidator()
        if schema_provider is None:
            from app.schema.provider import MockSchemaProvider
            schema_provider = MockSchemaProvider()

        self.generator = generator
        self.optimizer = optimizer
        self.explainer = explainer
        self.validator = validator
        self.schema_provider = schema_provider
        
        self.compiler = RunbookCompiler(
            self.generator,
            self.optimizer,
            self.explainer,
            self.validator,
            self.schema_provider
        )
        self.skill_compiler = AgentSkillCompiler()

        # Initialize storage services if enabled
        self.storage_enabled = settings.storage_enabled
        if self.storage_enabled:
            self.repo = repo or SQLiteRepository(settings.sqlite_db_path)
            self.bundle_store = bundle_store or BundleStore(self.repo)
            self.compilation_store = compilation_store or CompilationStore(self.repo)
            self.trace_store = trace_store or TraceStore(self.repo)
        else:
            self.repo = None
            self.bundle_store = None
            self.compilation_store = None
            self.trace_store = None

    def compile_runbook(self, content: str, filename: str, owner_id: str = "system", tenant_id: str = "system") -> CompileRunbookResponse:
        """
        Parses, validates, compiles, and packages a runbook configuration,
        optionally persisting the resulting bundles, compilation stats, and step traces.
        """
        start_time = time.perf_counter()
        errors: List[str] = []
        warnings: List[str] = []

        # 1. Parse content
        try:
            # Default to Markdown, fall back to plain text if needed
            if content.strip().startswith("#") or filename.endswith(".md"):
                runbook = MarkdownParser.parse(content)
            else:
                runbook = TextParser.parse(content)
        except Exception as e:
            # Return empty/stub bundle if parsing fails completely
            stub_manifest = AgentSkillManifest(
                skill_name="Failed Compile Parse",
                created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                overall_confidence=0.0
            )
            # Create a mock agent skill graph
            from app.agent.graph import ExecutionGraph
            from app.agent.governance import GovernancePolicy, ExecutionMode
            from app.domain.models import AgentSkill
            
            stub_skill = AgentSkill(
                name="Parse Failed Skill",
                source_runbook=filename,
                graph=ExecutionGraph(nodes=[], edges=[]),
                governance=GovernancePolicy(
                    approval_required=True,
                    execution_mode=ExecutionMode.MANUAL
                )
            )
            stub_bundle = SkillBundle(
                manifest=stub_manifest,
                agent_skill=stub_skill,
                diagnostics={"errors": [f"Parsing failed: {str(e)}"], "warnings": []},
                traces=[]
            )
            
            response = CompileRunbookResponse(
                status="FAILED",
                runbook_name=filename,
                bundle=stub_bundle,
                errors=[f"Parsing failed: {str(e)}"],
                warnings=[]
            )
            
            # Persist failure if storage is enabled
            if self.storage_enabled:
                try:
                    bundle_rec = self.bundle_store.save_bundle(filename, stub_bundle, "FAILED", owner_id=owner_id, tenant_id=tenant_id)
                    compilation_id = str(uuid.uuid4())
                    duration_ms = (time.perf_counter() - start_time) * 1000.0
                    comp_rec = CompilationRecord(
                        compilation_id=compilation_id,
                        bundle_id=bundle_rec.bundle_id,
                        timestamp=bundle_rec.created_at,
                        duration_ms=duration_ms,
                        confidence=0.0,
                        status="FAILED",
                        tenant_id=tenant_id,
                    )
                    self.compilation_store.save_compilation(comp_rec, tenant_id=tenant_id)
                    self.trace_store.save_traces(compilation_id, stub_bundle.traces, tenant_id=tenant_id)
                except Exception as ex:
                    from app.observability.logging import logger
                    logger.error(f"Failed to persist parsing failure: {str(ex)}", exc_info=True)

            return response

        # 2. Run Semantic Validation
        validation_res = RunbookValidator.validate(runbook)
        if not validation_res.is_valid:
            errors.extend(validation_res.errors)

        # 3. Execute compilation pipeline
        comp_result = self.compiler.compile(runbook)

        # Collect compiler diagnostics
        for comp_err in comp_result.errors:
            errors.append(f"Step '{comp_err.step_id}' error: {comp_err.message}")
        for comp_warn in comp_result.warnings:
            warnings.append(f"Step '{comp_warn.step_id}' warning: {comp_warn.message}")

        # 4. Generate Agent Skill Graph & Governance Policy
        agent_skill = self.skill_compiler.compile_skill(runbook, comp_result)

        # Calculate average confidence across steps
        avg_confidence = 1.0
        if comp_result.steps:
            avg_confidence = sum(s.confidence for s in comp_result.steps) / len(comp_result.steps)

        # 5. Build Manifest & Skill Bundle
        manifest = AgentSkillManifest(
            skill_name=f"{runbook.name} Skill",
            compiler_version="1.0.0",
            created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            overall_confidence=round(avg_confidence, 2)
        )

        bundle = SkillBundle(
            manifest=manifest,
            agent_skill=agent_skill,
            diagnostics={
                "errors": errors,
                "warnings": warnings
            },
            traces=comp_result.traces
        )

        # Override status if validations failed beforehand
        final_status = "FAILED" if (not validation_res.is_valid or comp_result.status == CompilationStatus.FAILED) else comp_result.status.value

        response = CompileRunbookResponse(
            status=final_status,
            runbook_name=runbook.name,
            bundle=bundle,
            errors=errors,
            warnings=warnings
        )

        # Persist success/partial/failure if storage is enabled
        if self.storage_enabled:
            try:
                bundle_rec = self.bundle_store.save_bundle(runbook.name, bundle, final_status, owner_id=owner_id, tenant_id=tenant_id)
                compilation_id = str(uuid.uuid4())
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                comp_rec = CompilationRecord(
                    compilation_id=compilation_id,
                    bundle_id=bundle_rec.bundle_id,
                    timestamp=bundle_rec.created_at,
                    duration_ms=duration_ms,
                    confidence=bundle.manifest.overall_confidence,
                    status=final_status,
                    tenant_id=tenant_id,
                )
                self.compilation_store.save_compilation(comp_rec, tenant_id=tenant_id)
                self.trace_store.save_traces(compilation_id, bundle.traces, tenant_id=tenant_id)
            except Exception as ex:
                from app.observability.logging import logger
                logger.error(f"Failed to persist compilation: {str(ex)}", exc_info=True)

        return response
