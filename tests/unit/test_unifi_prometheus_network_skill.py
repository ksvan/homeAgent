from pathlib import Path

from app.agent.skills import SkillRegistry


def test_unifi_prometheus_network_skill_is_discoverable() -> None:
    skills_dir = Path(__file__).parents[2] / "app" / "skills"
    registry = SkillRegistry()
    registry.load(skills_dir)

    skill = registry.get("unifi-prometheus-network")

    assert skill is not None
    assert skill.display_name == "UniFi Network Operations"
    assert skill.short_description == (
        "Diagnose household UniFi and Wi-Fi performance from Prometheus"
    )
    assert skill.has_scripts is True
    assert skill.has_references is False
    assert "unifi-prometheus-network" in registry.skills_index_text()
    content = registry.get_content("unifi-prometheus-network")
    assert content is not None
    assert "Blackbox Exporter Probes" in content
    assert "Active Point-in-Time Tests" in content
    assert "Failure-Domain Workflow" in content
    assert "Event Classification and Safe Investigation" in content
    assert "probe_success" in content
