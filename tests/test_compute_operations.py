from __future__ import annotations

import threading
from datetime import UTC, datetime

from app.compute.service import ComputeOperationsService
from app.core.clock import FrozenClock
from app.database import close_connection, get_connection, init_db


TEMPLATE = {
    "code": "solver-a",
    "name": "方程求解模板",
    "algorithm": "solver-a",
    "parameter_schema": {
        "iterations": {"type": "integer", "required": True, "minimum": 1, "maximum": 10000},
        "tolerance": {"type": "number", "required": False, "minimum": 0.0, "maximum": 1.0},
        "mode": {"type": "string", "required": True, "choices": ["fast", "accurate"]},
    },
    "default_parameters": {"tolerance": 0.001},
    "max_runtime_seconds": 300,
    "max_attempts": 2,
}


def submit_payload(key: str, *, user: str = "researcher-1", priority: int = 50) -> dict:
    return {
        "template_code": "solver-a",
        "project_code": "project-a",
        "requested_by": user,
        "parameters": {"iterations": 100, "mode": "accurate"},
        "priority": priority,
        "idempotency_key": key,
    }


def create_template(client) -> None:
    response = client.post("/api/compute/templates?actor=administrator", json=TEMPLATE)
    assert response.status_code == 201, response.text


def test_template_submission_idempotency_and_parameter_validation(client):
    create_template(client)
    first = client.post("/api/compute/tasks", json=submit_payload("request-000001"))
    second = client.post("/api/compute/tasks", json=submit_payload("request-000001"))
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    invalid = submit_payload("request-000002")
    invalid["parameters"]["iterations"] = 20000
    rejected = client.post("/api/compute/tasks", json=invalid)
    assert rejected.status_code == 422


def test_priority_capability_claim_and_result_version(client):
    create_template(client)
    low = client.post("/api/compute/tasks", json=submit_payload("priority-low", priority=10)).json()
    high = client.post("/api/compute/tasks", json=submit_payload("priority-high", priority=90)).json()
    no_match = client.post("/api/compute/tasks/claim", json={"worker_id": "w0", "capabilities": ["other"], "lease_seconds": 60})
    assert no_match.status_code == 200 and no_match.json()["task"] is None
    claimed = client.post("/api/compute/tasks/claim", json={"worker_id": "w1", "capabilities": ["solver-a"], "lease_seconds": 60})
    assert claimed.status_code == 200
    assert claimed.json()["task"]["id"] == high["id"]
    completed = client.post(
        f"/api/compute/tasks/{high['id']}/complete",
        json={"worker_id": "w1", "result": {"value": 3.14}, "metrics": {"seconds": 2}},
    )
    assert completed.status_code == 200
    details = client.get(f"/api/compute/task-details/{high['id']}").json()
    assert details["status"] == "succeeded"
    assert details["current_result_version"] == 1
    assert len(details["results"]) == 1
    assert low["status"] == "queued"


def test_quota_cancel_retry_priority_and_batch_interventions(client):
    create_template(client)
    quota = client.put(
        "/api/compute/quotas?actor=administrator",
        json={"subject_type": "user", "subject_key": "limited", "max_queued": 1, "max_running": 1, "daily_submissions": 2},
    )
    assert quota.status_code == 200
    one = client.post("/api/compute/tasks", json=submit_payload("quota-one", user="limited")).json()
    blocked = client.post("/api/compute/tasks", json=submit_payload("quota-two", user="limited"))
    assert blocked.status_code == 409
    cancelled = client.post(f"/api/compute/tasks/{one['id']}/cancel", json={"actor": "administrator", "reason": "项目暂停"})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    retried = client.post(f"/api/compute/tasks/{one['id']}/retry", json={"actor": "administrator", "reason": "项目恢复", "priority": 95})
    assert retried.status_code == 200 and retried.json()["priority"] == 95
    other = client.post("/api/compute/tasks", json=submit_payload("batch-other", user="other-user")).json()
    batch = client.post(
        "/api/compute/tasks/batch",
        json={"task_ids": [one["id"], other["id"]], "operation": "priority", "actor": "administrator", "reason": "紧急算例", "priority": 99},
    )
    assert batch.status_code == 200
    assert len(batch.json()["succeeded"]) == 2
    details = client.get(f"/api/compute/task-details/{one['id']}").json()
    assert [item["action"] for item in details["interventions"]] == ["cancel", "retry", "priority"]


def test_failure_backoff_and_expired_lease_recovery(client):
    from app.database import init_db

    init_db()
    clock = FrozenClock(datetime(2026, 9, 26, 2, 0, tzinfo=UTC))
    service = ComputeOperationsService(get_connection(), clock)
    service.create_template(TEMPLATE, "administrator")
    first = service.submit(submit_payload("failure-000001"))
    claimed = service.claim("worker-a", ["solver-a"], 10)
    assert claimed and claimed["id"] == first["id"]
    failed = service.fail(first["id"], "worker-a", "numeric_error", "数值不收敛", True)
    assert failed["status"] == "queued"
    assert failed["available_at"] > failed["updated_at"]
    clock.advance(seconds=2)
    claimed_again = service.claim("worker-a", ["solver-a"], 10)
    assert claimed_again and claimed_again["attempt_count"] == 2
    clock.advance(seconds=11)
    recovered = service.recover_expired()
    assert recovered["exhausted"] == [first["id"]]
    details = service.get_task(first["id"])
    assert details["status"] == "failed"
    assert details["interventions"][-1]["action"] == "lease_recovery"


def _direct_service(clock=None) -> ComputeOperationsService:
    init_db()
    service = ComputeOperationsService(get_connection(), clock)
    service.create_template(TEMPLATE, "administrator")
    return service


def _set_quota(service: ComputeOperationsService, key: str, *, max_running: int, max_queued: int = 100, daily: int = 1000) -> None:
    service.set_quota(
        {"subject_type": "user", "subject_key": key, "max_queued": max_queued, "max_running": max_running, "daily_submissions": daily},
        "administrator",
    )


def test_running_quota_is_enforced_at_claim_boundary_not_submit(client):
    service = _direct_service()
    _set_quota(service, "stu-a", max_running=1)
    first = service.submit(submit_payload("a-0001", user="stu-a"))
    claimed = service.claim("w1", ["solver-a"], 60)
    assert claimed and claimed["id"] == first["id"]
    # 已有任务运行时仍允许入队：并行上限不再在提交时拦截，而在领取时裁决。
    second = service.submit(submit_payload("a-0002", user="stu-a"))
    assert second["status"] == "queued"
    assert service.claim("w2", ["solver-a"], 60) is None
    service.complete(first["id"], "w1", {"value": 1}, {})
    freed = service.claim("w2", ["solver-a"], 60)
    assert freed and freed["id"] == second["id"]


def test_claim_skips_full_head_account_and_keeps_queue_moving(client):
    service = _direct_service()
    _set_quota(service, "stu-a", max_running=1)
    _set_quota(service, "stu-b", max_running=1)
    hold = service.submit(submit_payload("a-hold", user="stu-a"))
    held = service.claim("w1", ["solver-a"], 60)
    assert held["id"] == hold["id"]
    # 队首属于已满的 stu-a，其后是 stu-b，再后才是 stu-a 的第二条。
    a1 = service.submit(submit_payload("a-queued-1", user="stu-a"))
    b1 = service.submit(submit_payload("b-queued-1", user="stu-b"))
    a2 = service.submit(submit_payload("a-queued-2", user="stu-a"))
    got = service.claim("w2", ["solver-a"], 60)
    assert got and got["id"] == b1["id"]  # 跳过满员账号，不阻塞其他学员
    service.complete(held["id"], "w1", {"value": 1}, {})
    nxt = service.claim("w1", ["solver-a"], 60)
    assert nxt and nxt["id"] == a1["id"]  # 名额释放后按入队顺序领取
    service.complete(nxt["id"], "w1", {"value": 2}, {})
    last = service.claim("w3", ["solver-a"], 60)
    assert last and last["id"] == a2["id"]


def test_running_slot_released_after_permanent_failure_and_retry(client):
    clock = FrozenClock(datetime(2026, 9, 26, 2, 0, tzinfo=UTC))
    service = _direct_service(clock)
    _set_quota(service, "stu-a", max_running=1)
    first = service.submit(submit_payload("a-fail-1", user="stu-a"))
    claimed = service.claim("w1", ["solver-a"], 60)
    assert claimed and claimed["id"] == first["id"]
    second = service.submit(submit_payload("a-fail-2", user="stu-a"))
    assert service.claim("w2", ["solver-a"], 60) is None
    failed = service.fail(first["id"], "w1", "fatal", "不可恢复错误", False)
    assert failed["status"] == "failed"
    freed = service.claim("w2", ["solver-a"], 60)
    assert freed and freed["id"] == second["id"]
    # 可重试失败：任务离开 running 回到队列，同一名额可被再次领取。
    retried = service.fail(second["id"], "w2", "numeric_error", "稍后重试", True)
    assert retried["status"] == "queued"
    clock.advance(seconds=2)  # 越过退避窗口
    requeued = service.claim("w3", ["solver-a"], 60)
    assert requeued and requeued["id"] == second["id"] and requeued["attempt_count"] == 2


def test_running_slot_released_after_cancel_requested(client):
    service = _direct_service()
    _set_quota(service, "stu-a", max_running=1)
    first = service.submit(submit_payload("a-cancel-1", user="stu-a"))
    claimed = service.claim("w1", ["solver-a"], 60)
    second = service.submit(submit_payload("a-cancel-2", user="stu-a"))
    assert service.claim("w2", ["solver-a"], 60) is None
    cancelled = service.cancel(first["id"], "administrator", "学员主动取消")
    assert cancelled["status"] == "cancel_requested"  # 运行中任务只请求取消，仍由持有者回执
    freed = service.claim("w2", ["solver-a"], 60)
    assert freed and freed["id"] == second["id"]  # cancel_requested 不再占用名额


def test_running_slot_released_when_claim_reclaims_expired_lease(client):
    clock = FrozenClock(datetime(2026, 9, 26, 2, 0, tzinfo=UTC))
    service = _direct_service(clock)
    service.create_template({**TEMPLATE, "code": "solver-once", "algorithm": "solver-once", "max_attempts": 1}, "administrator")
    _set_quota(service, "stu-a", max_running=1)
    first_payload = submit_payload("a-expired-1", user="stu-a")
    first_payload["template_code"] = "solver-once"
    second_payload = submit_payload("a-expired-2", user="stu-a")
    second_payload["template_code"] = "solver-once"
    first = service.submit(first_payload)
    claimed = service.claim("w1", ["solver-once"], 10)
    assert claimed and claimed["id"] == first["id"]
    second = service.submit(second_payload)
    assert service.claim("w2", ["solver-once"], 10) is None
    clock.advance(seconds=11)
    # 领取事务内先回收过期租约（重试次数耗尽 -> failed），名额立即释放。
    freed = service.claim("w2", ["solver-once"], 10)
    assert freed and freed["id"] == second["id"]
    expired_task = service.get_task(first["id"])
    assert expired_task["status"] == "failed"
    assert expired_task["last_error_code"] == "lease_expired"


def test_quota_adjustment_only_affects_future_claims(client):
    service = _direct_service()
    _set_quota(service, "stu-a", max_running=2)
    tasks = [service.submit(submit_payload(f"a-adjust-{i}", user="stu-a")) for i in range(4)]
    running = [service.claim(f"w{i}", ["solver-a"], 60) for i in range(2)]
    assert {item["id"] for item in running} == {tasks[0]["id"], tasks[1]["id"]}
    assert service.claim("w3", ["solver-a"], 60) is None
    # 调低上限：已有运行不被中断，但新的领取仍被挡住。
    _set_quota(service, "stu-a", max_running=1)
    assert service.claim("w4", ["solver-a"], 60) is None
    assert {item["status"] for item in (service.get_task(tasks[0]["id"]), service.get_task(tasks[1]["id"]))} == {"running"}
    # 调高上限：只放开后续领取。
    _set_quota(service, "stu-a", max_running=3)
    third = service.claim("w5", ["solver-a"], 60)
    assert third and third["id"] == tasks[2]["id"]


def test_concurrent_claims_never_exceed_quota_and_queue_keeps_advancing(client):
    worker_count = 6
    students = ["stu-a", "stu-b", "stu-c"]
    per_student = 3
    service = _direct_service()
    for student in students:
        _set_quota(service, student, max_running=1)
    # 固定顺序入队：每名学员连续三条，共 9 条，优先级一致。
    task_ids: dict[str, list[int]] = {student: [] for student in students}
    for student in students:
        for index in range(per_student):
            task = service.submit(submit_payload(f"{student}-{index}", user=student))
            task_ids[student].append(int(task["id"]))

    claimed_order: dict[str, list[int]] = {student: [] for student in students}

    def concurrent_claim_wave() -> list[dict]:
        barrier = threading.Barrier(worker_count)
        results: list[dict] = []
        lock = threading.Lock()

        def worker(worker_index: int) -> None:
            try:
                worker_service = ComputeOperationsService()
                barrier.wait()
                task = worker_service.claim(f"worker-{worker_index}", ["solver-a"], 60)
                with lock:
                    results.append({"worker": f"worker-{worker_index}", "task": task})
            except BaseException as exc:  # noqa: BLE001 - 让主线程看到工作线程异常
                with lock:
                    results.append({"worker": f"worker-{worker_index}", "error": exc})
            finally:
                close_connection()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(worker_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return results

    # 每条任务恰好一个 wave：3 名学员各占一个名额，6 个工作者同时抢，只有 3 个成功。
    for wave in range(per_student):
        results = concurrent_claim_wave()
        assert all("error" not in item for item in results), results
        claimed = [item["task"] for item in results if item["task"] is not None]
        assert len(claimed) == len(students)
        by_student = {task["requested_by"]: task for task in claimed}
        assert set(by_student) == set(students)  # 任一时刻每名学员至多一个运行
        running_rows = service.connection.execute(
            "SELECT requested_by,COUNT(*) AS amount FROM compute_tasks WHERE status='running' GROUP BY requested_by"
        ).fetchall()
        assert {row["requested_by"]: row["amount"] for row in running_rows} == {student: 1 for student in students}
        # 完成本波任务前，再来的并发请求一个名额都拿不到。
        blocked = concurrent_claim_wave()
        assert all("error" not in item for item in blocked), blocked
        assert [item["task"] for item in blocked] == [None] * worker_count
        for student, task in by_student.items():
            claimed_order[student].append(int(task["id"]))
            service.complete(int(task["id"]), task["lease_owner"], {"value": wave}, {})

    # 队列持续前进：每名学员严格按入队顺序领取，且全部任务最终成功。
    assert claimed_order == task_ids
    states = {row["status"]: row["amount"] for row in service.connection.execute(
        "SELECT status,COUNT(*) AS amount FROM compute_tasks GROUP BY status"
    ).fetchall()}
    assert states == {"succeeded": per_student * len(students)}

