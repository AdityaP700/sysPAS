import pytest
from app.actions.ticket import TicketConnector


def test_ticket_connector_create_validation():
    connector = TicketConnector()
    
    # Missing title
    with pytest.raises(ValueError) as exc:
        connector.validate({"action": "CREATE_TICKET", "description": "some details"})
    assert "title' is required" in str(exc.value)

    # Missing description
    with pytest.raises(ValueError) as exc:
        connector.validate({"action": "CREATE_TICKET", "title": "some title"})
    assert "description' is required" in str(exc.value)


def test_ticket_connector_update_validation():
    connector = TicketConnector()
    
    # Missing ticket_id
    with pytest.raises(ValueError) as exc:
        connector.validate({"action": "UPDATE_TICKET", "comment": "updating comment"})
    assert "ticket_id' is required" in str(exc.value)

    # Missing fields to update
    with pytest.raises(ValueError) as exc:
        connector.validate({"action": "UPDATE_TICKET", "ticket_id": "INC-12345"})
    assert "Must provide at least one field" in str(exc.value)


def test_ticket_connector_create_execute():
    connector = TicketConnector()
    payload = {
        "action": "CREATE_TICKET",
        "title": "Severe Login Failure Loop",
        "description": "500 login failure events detected within a 5-minute window",
        "priority": "Critical"
    }
    
    res = connector.execute(payload)
    assert res.success is True
    assert res.action_type == "CREATE_TICKET"
    assert res.external_id.startswith("INC-")
    assert res.details["title"] == "Severe Login Failure Loop"
    assert res.details["priority"] == "Critical"
    assert res.details["status"] == "OPEN"


def test_ticket_connector_update_execute():
    connector = TicketConnector()
    payload = {
        "action": "UPDATE_TICKET",
        "ticket_id": "INC-88888",
        "comment": "Adding resolution comment"
    }
    
    res = connector.execute(payload)
    assert res.success is True
    assert res.action_type == "UPDATE_TICKET"
    assert res.external_id == "INC-88888"
    assert "comment" in res.details["updated_fields"]
    assert res.details["status"] == "UPDATED"
