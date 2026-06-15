from app.templates.registry import SPLTemplateRegistry


def test_template_registry_lookup():
    temp_failed = SPLTemplateRegistry.get_template("FAILED_LOGIN")
    assert "status_filter" in temp_failed
    assert "group_clause" in temp_failed
    
    temp_generic = SPLTemplateRegistry.get_template("GENERIC")
    assert "head 100" in temp_generic
    
    # Fallback check
    temp_fallback = SPLTemplateRegistry.get_template("UNKNOWN_INTENT")
    assert "head 100" in temp_fallback


def test_template_formatting():
    temp = SPLTemplateRegistry.get_template("FAILED_LOGIN")
    
    formatted = temp.format(
        index="auth_logs",
        status_filter="status=failed",
        group_clause="user, src_ip"
    )
    
    assert "index=auth_logs" in formatted
    assert "status=failed" in formatted
    assert "stats count by user, src_ip" in formatted


def test_new_attack_templates():
    powershell_temp = SPLTemplateRegistry.get_template("POWERSHELL_ACTIVITY")
    assert "powershell.exe" in powershell_temp
    
    cred_dump_temp = SPLTemplateRegistry.get_template("CREDENTIAL_DUMPING")
    assert "mimikatz" in cred_dump_temp
    
    persistence_temp = SPLTemplateRegistry.get_template("PERSISTENCE")
    assert "currentversion" in persistence_temp
    
    registry_temp = SPLTemplateRegistry.get_template("REGISTRY_MODIFICATION")
    assert "reg add" in registry_temp
    
    account_temp = SPLTemplateRegistry.get_template("ACCOUNT_CREATION")
    assert "net user" in account_temp
