"""Operations view: activity feed, agent success rates and the live event stream."""
from conftest import upload


def test_ops_summary_shows_agents_activity_and_leakage(meena):
    upload(meena, "borrower_batch_A.csv")
    meena.post("/api/daemon/run")
    meena.post("/api/recon/run")
    s = meena.get("/api/ops").json()
    agents = {a["agent"] for a in s["agents"]}
    assert {"Recovery daemon", "Recovery agent", "Importer"} <= agents
    assert s["leakage_findings"] == 12 and s["leakage_paise"] == 8251318
    assert 0 < s["success_rate"] <= 1 and s["runs_24h"] > 0
    assert any(i["agent"] == "Recovery daemon" and "Awaiting approval" in i["text"] for i in s["activity"])


def test_ops_is_per_merchant(meena, arun):
    upload(meena, "borrower_batch_A.csv")
    meena.post("/api/daemon/run")
    assert not any(i["agent"] == "Recovery daemon" for i in arun.get("/api/ops").json()["activity"])


def test_leakage_survives_a_restart(meena):
    from app import recon
    meena.post("/api/recon/run")
    recon.LAST_RUN.clear()
    assert meena.get("/api/ops").json()["leakage_paise"] == 8251318
