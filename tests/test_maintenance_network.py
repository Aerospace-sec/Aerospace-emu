from sim.maintenance_network_lab import LoadPackage, MaintenanceNetwork, MaintenanceSession, NetworkZone, run_scenarios


def test_maintenance_boundary_requires_authorized_dual_approval():
    network = MaintenanceNetwork((NetworkZone("MAINTENANCE", 20, "controlled"), NetworkZone("AVIONICS", 30, "safety")))
    session = MaintenanceSession("s", "MAINTENANCE", "maintenance", True, True, False)
    assert network.boundary(session, "AVIONICS").reason == "cross_domain_policy"
    assert network.boundary(MaintenanceSession("s2", "MAINTENANCE", "maintenance", True, True, True), "AVIONICS").accepted


def test_maintenance_protocol_negative_paths():
    result = run_scenarios()["attacks"]
    assert result["cabin_boundary"]["reason"] == "cross_domain_policy"
    assert result["unsigned_load"]["reason"] == "package_signature"
    assert result["udp_replay"]["reason"] == "replay"
    assert result["snmp_unauthorized_write"]["reason"] == "management_write_authorization"
    assert result["snmp_legacy_version"]["reason"] == "management_authentication"


def test_maintenance_normal_path_is_staged_not_activated():
    normal = run_scenarios()["normal"]
    assert normal["load"]["accepted"] is True
    assert normal["load"]["action"] == "staged_not_activated"
    assert normal["udp"]["action"] == "deliver_semantic_data"
