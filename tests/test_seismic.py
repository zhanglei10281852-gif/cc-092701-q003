from __future__ import annotations


def event_payload():
    return {
        "external_id": "EQ-TEST-001",
        "origin_time": "2026-09-24T12:00:00+00:00",
        "latitude": 30.1,
        "longitude": 103.2,
        "depth_km": 12.0,
        "magnitude": 5.8,
        "magnitude_type": "ML",
        "source": "test",
    }


def test_seismic_event_observation_and_computation(client):
    created = client.post("/api/seismic/events", json=event_payload())
    assert created.status_code == 201, created.text
    event_id = created.json()["id"]
    observation = client.post(f"/api/seismic/events/{event_id}/observations", json={"station_code": "SC01", "channel": "HNZ", "observed_at": "2026-09-24T12:00:03+00:00", "pga": 0.8, "pgv": 2.1, "distance_km": 18})
    assert observation.status_code == 201
    task = client.post(f"/api/seismic/events/{event_id}/computations", json={"model_version": "test-1", "grid_step_km": 20, "radius_km": 20, "requested_by": "test"})
    assert task.status_code == 202
    claimed = client.post("/api/seismic/computations/claim?worker_id=test-worker")
    assert claimed.status_code == 200
    task_id = claimed.json()["task"]["id"]
    result = client.post(f"/api/seismic/computations/{task_id}/calculate?worker_id=test-worker")
    assert result.status_code == 200
    assert result.json()["status"] == "done"
    assert result.json()["result_json"]


def test_duplicate_observation_is_idempotent(client):
    event = client.post("/api/seismic/events", json={**event_payload(), "external_id": "EQ-TEST-002"}).json()
    payload = {"station_code": "SC02", "channel": "HNZ", "observed_at": "2026-09-24T12:00:03+00:00", "pga": 0.8, "distance_km": 18}
    first = client.post(f"/api/seismic/events/{event['id']}/observations", json=payload)
    second = client.post(f"/api/seismic/events/{event['id']}/observations", json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
