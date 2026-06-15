import re
from typing import Tuple
from app.context.generation_context import GenerationContext
from app.templates.registry import SPLTemplateRegistry
from app.templates.mappings import IntentMapper, Intent


class TemplateGenerationEngine:
    """Combines template definitions with schema grounding and parameters to generate SPL queries."""

    def generate_spl(self, context: GenerationContext) -> Tuple[str, str, float]:
        step = context.step
        description = step.description
        
        # 1. Map intent
        intent = IntentMapper.map_description_to_intent(description)
        template = SPLTemplateRegistry.get_template(intent.value)

        # 2. Schema Grounding
        # Read resolved fields from context metadata
        grounding_meta = context.metadata.get("grounding", {})
        resolved = grounding_meta.get("resolved_fields", [])
        
        ip_field = "src_ip"
        user_field = "user"
        generator_confidence = 1.0

        # Ground IP field
        if "src_ip" in resolved:
            ip_field = "src_ip"
        elif "ip_address" in resolved:
            ip_field = "ip_address"
        elif "src_ip" in context.schema_fields:
            ip_field = "src_ip"
        else:
            # Fall back to default and penalize confidence
            ip_field = "src_ip"
            if intent in [Intent.FAILED_LOGIN, Intent.BRUTE_FORCE, Intent.SUSPICIOUS_IP, Intent.THREAT_LOOKUP]:
                generator_confidence = 0.85

        # Ground User field
        if "user" in resolved:
            user_field = "user"
        elif "user" in context.schema_fields:
            user_field = "user"
        else:
            # Fall back
            user_field = "user"
            if intent in [Intent.FAILED_LOGIN, Intent.BRUTE_FORCE]:
                generator_confidence = min(generator_confidence, 0.85)

        # 3. Dynamic schema-grounded clauses
        resolved_group_fields = []
        for f in resolved:
            matched = None
            for sf in (context.schema_fields or []):
                if sf.lower() == f.lower():
                    matched = sf
                    break
            if matched and matched not in resolved_group_fields:
                resolved_group_fields.append(matched)

        schema_fields_lower = [f.lower() for f in (context.schema_fields or [])]

        if not resolved_group_fields and context.schema_fields:
            if intent == Intent.BRUTE_FORCE:
                if "src_ip" in schema_fields_lower:
                    resolved_group_fields.append("src_ip")
                elif "ip_address" in schema_fields_lower:
                    resolved_group_fields.append("ip_address")
                elif "host" in schema_fields_lower:
                    resolved_group_fields.append("host")
                if "user" in schema_fields_lower:
                    resolved_group_fields.append("user")
                elif "username" in schema_fields_lower:
                    resolved_group_fields.append("username")
            else:
                if "user" in schema_fields_lower:
                    resolved_group_fields.append("user")
                elif "username" in schema_fields_lower:
                    resolved_group_fields.append("username")
                if "src_ip" in schema_fields_lower:
                    resolved_group_fields.append("src_ip")
                elif "ip_address" in schema_fields_lower:
                    resolved_group_fields.append("ip_address")
                elif "host" in schema_fields_lower:
                    resolved_group_fields.append("host")

        if not resolved_group_fields:
            if intent == Intent.BRUTE_FORCE:
                resolved_group_fields = ["src_ip", "user"]
            else:
                resolved_group_fields = ["user", "src_ip"]

        group_clause = ", ".join(resolved_group_fields)

        status_filter = "action=failure"
        if "action" in schema_fields_lower:
            status_filter = "action=failure"
        elif "status" in schema_fields_lower:
            status_filter = "status=failed"

        # 4. Parameter extraction
        time_window = context.constraints.get("time_window") or "5m"
        
        # Format time window as earliest filter
        if not time_window.startswith("-"):
            earliest_val = f"-{time_window}"
        else:
            earliest_val = time_window

        threshold = "100"
        if step.threshold:
            match = re.search(r'\d+', step.threshold)
            if match:
                threshold = match.group()

        index = context.data_source or "main"

        # 5. Format query
        try:
            grounded_spl = template.format(
                index=index,
                ip_field=ip_field,
                user_field=user_field,
                time_window=earliest_val,
                threshold=threshold,
                suspicious_ips="'127.0.0.1', '192.168.1.100'",
                step_id=step.step_id,
                status_filter=status_filter,
                group_clause=group_clause
            )
        except Exception:
            # Fallback to generic formatted query
            grounded_spl = f"index={index} | head 100"
            generator_confidence = 0.5

        return grounded_spl, intent.value, generator_confidence
