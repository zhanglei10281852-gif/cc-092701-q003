"""并发领取下的学员运行配额保证。

通过多学员、多工作者在固定屏障点同时发起领取请求，证明：
1. 任一学员在任一瞬间的运行任务数都不会超过 max_running；
2. 队首学员名额已满时会被跳过，其他学员的任务仍可持续被领取（队列不被队首阻塞）；
3. 完成、失败、取消确认、租约恢复后名额立即释放，队列继续前进；
4. 配额上调允许后续领取更多、下调不强制中断已有运行，只影响后续领取。
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.compute.service import ComputeOperationsService
from app.core.clock import to_storage
from app.database import close_connection, get_connection, init_db

CAPS = {"student-a": 1, "student-b": 1, "student-c": 2}

TEMPLATE = {
    "code": "solver-a",
    "name": "方程求解模板",
    "algorithm": "solver-a",
    "parameter_schema": {"iterations": {"type": "integer", "required": True, "minimum": 1, "maximum": 10000}},
    "default_parameters": {},
    "max_runtime_seconds": 300,
    "max_attempts": 2,
}


@pytest.fixture()
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ComputeOperationsService:
    monkeypatch.setenv("TOWNSHIP_DATABASE_PATH", str(tmp_path / "concurrent.db"))
    close_connection()
    init_db()
    svc = ComputeOperationsService(get_connection())
    svc.create_template(TEMPLATE, "administrator")
    for student, cap in CAPS.items():
        svc.set_quota(
            {"subject_type": "user", "subject_key": student, "max_queued": 20, "max_running": cap, "daily_submissions": 1000},
            "administrator",
        )
    return svc


def submit(service: ComputeOperationsService, key: str, user: str, priority: int = 50) -> dict:
    return service.submit(
        {
            "template_code": "solver-a",
            "project_code": "project-a",
            "requested_by": user,
            "parameters": {"iterations": 100},
            "priority": priority,
            "idempotency_key": key,
        }
    )


def assert_invariant(connection) -> dict[str, str]:
    """核对当前数据库中每个学员的运行数都未超过其（可能被调整过的）上限。"""
    rows = connection.execute(
        "SELECT requested_by, COUNT(*) AS amount FROM compute_tasks WHERE status='running' GROUP BY requested_by"
    ).fetchall()
    running = {str(row["requested_by"]): int(row["amount"]) for row in rows}
    for student, observed in running.items():
        quota = connection.execute(
            "SELECT max_running FROM compute_quotas WHERE subject_type='user' AND subject_key=?",
            (student,),
        ).fetchone()
        cap = int(quota["max_running"]) if quota is not None else observed
        assert observed <= cap, f"{student} 运行数 {observed} 超过上限 {cap}"
    return running


def concurrent_claims(worker_ids: list[str], barrier: threading.Barrier | None = None) -> list[str | None]:
    """让多个工作者在同一屏障点发起领取，返回各自领取到的任务幂等键。"""
    results: dict[str, str | None] = {}
    errors: list[BaseException] = []

    def worker(worker_id: str) -> None:
        try:
            own = ComputeOperationsService()
            if barrier is not None:
                barrier.wait()
            task = own.claim(worker_id, ["solver-a"], 60)
            if task is not None:
                assert_invariant(own.connection)
                results[worker_id] = task["idempotency_key"]
            else:
                results[worker_id] = None
        except BaseException as exc:  # noqa: BLE001 - 把线程内异常带回主线程断言
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(wid,)) for wid in worker_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
    return [results[wid] for wid in worker_ids]


def test_concurrent_claims_never_exceed_cap_and_skip_full_head_of_line(service: ComputeOperationsService):
    # 固定提交顺序：A1 高优先级位列队首，A2 紧随；B、C 学员任务在其后。
    submit(service, "task-a1", "student-a", priority=90)
    submit(service, "task-b1", "student-b")
    submit(service, "task-a2", "student-a")
    submit(service, "task-b2", "student-b")
    submit(service, "task-c1", "student-c")
    submit(service, "task-c2", "student-c")

    # 四名工作者同时领取：A1（队首）被领后 A 名额即满，A2 必须被跳过，
    # B、C 仍可持续领取，C 上限为 2 可占两个名额。
    claimed = concurrent_claims(["w0", "w1", "w2", "w3"], threading.Barrier(4))
    assert sorted(k for k in claimed if k) == ["task-a1", "task-b1", "task-c1", "task-c2"]
    assert_invariant(service.connection)

    # 队首的 A2 与 B2 因名额占满仍在排队，而没有阻塞同批的 B、C 任务。
    assert service.repository.task_by_idempotency("student-a", "task-a2")["status"] == "queued"
    assert service.repository.task_by_idempotency("student-b", "task-b2")["status"] == "queued"


def task_id(service: ComputeOperationsService, key: str, user: str) -> int:
    return int(service.repository.task_by_idempotency(user, key)["id"])


def owner_of(service: ComputeOperationsService, key: str, user: str) -> str:
    return str(service.repository.task_by_idempotency(user, key)["lease_owner"])


def test_slots_release_on_complete_fail_cancel_and_lease_recovery(service: ComputeOperationsService):
    submit(service, "task-a1", "student-a")
    submit(service, "task-a2", "student-a")
    submit(service, "task-b1", "student-b")
    submit(service, "task-b2", "student-b")
    submit(service, "task-c1", "student-c")
    submit(service, "task-c2", "student-c")

    first = {key for key in concurrent_claims(["w0", "w1", "w2", "w3"], threading.Barrier(4)) if key}
    assert first == {"task-a1", "task-b1", "task-c1", "task-c2"}

    # 四种释放路径，固定顺序执行：
    # 1) 完成
    service.complete(task_id(service, "task-a1", "student-a"), owner_of(service, "task-a1", "student-a"), {"value": 1}, {})
    assert_invariant(service.connection)
    # 2) 不可重试失败
    service.fail(task_id(service, "task-b1", "student-b"), owner_of(service, "task-b1", "student-b"), "boom", "不可恢复", False)
    assert_invariant(service.connection)
    # 3) 取消运行中任务并由工作者确认
    service.cancel(task_id(service, "task-c1", "student-c"), "administrator", "项目暂停")
    acknowledged = service.acknowledge_cancel(task_id(service, "task-c1", "student-c"), owner_of(service, "task-c1", "student-c"))
    assert acknowledged["status"] == "cancelled"
    assert_invariant(service.connection)
    # 4) 租约过期恢复（仍有重试次数 -> 回到队列，名额同样立即释放）
    c2_id = task_id(service, "task-c2", "student-c")
    past = to_storage(datetime.now(UTC) - timedelta(seconds=1))
    service.connection.execute("UPDATE compute_tasks SET lease_expires_at=? WHERE id=?", (past, c2_id))
    recovered = service.recover_expired()
    assert recovered["recovered"] == [c2_id]
    assert_invariant(service.connection)

    # 名额全部释放后，三名工作者同时领取，队列继续前进且仍不超上限。
    second = {key for key in concurrent_claims(["w4", "w5", "w6"], threading.Barrier(3)) if key}
    assert second == {"task-a2", "task-b2", "task-c2"}
    assert_invariant(service.connection)

    for key, user in [("task-a2", "student-a"), ("task-b2", "student-b"), ("task-c2", "student-c")]:
        service.complete(task_id(service, key, user), owner_of(service, key, user), {"value": 2}, {})
    assert_invariant(service.connection)

    # 队列排空：所有任务都到达终态。
    assert service.summary()["states"].get("queued", 0) == 0
    statuses = {
        key: service.repository.task_by_idempotency(user, key)["status"]
        for key, user in [
            ("task-a1", "student-a"), ("task-a2", "student-a"),
            ("task-b1", "student-b"), ("task-b2", "student-b"),
            ("task-c1", "student-c"), ("task-c2", "student-c"),
        ]
    }
    assert statuses == {
        "task-a1": "succeeded", "task-a2": "succeeded",
        "task-b1": "failed", "task-b2": "succeeded",
        "task-c1": "cancelled", "task-c2": "succeeded",
    }


def test_quota_adjustment_only_affects_future_claims(service: ComputeOperationsService):
    # 上调 A 的运行上限到 2：后续领取可以同时占两个名额。
    service.set_quota(
        {"subject_type": "user", "subject_key": "student-a", "max_queued": 20, "max_running": 2, "daily_submissions": 1000},
        "administrator",
    )
    submit(service, "task-a3", "student-a")
    submit(service, "task-a4", "student-a")
    claimed = {key for key in concurrent_claims(["w0", "w1"], threading.Barrier(2)) if key}
    assert claimed == {"task-a3", "task-a4"}
    assert assert_invariant(service.connection)["student-a"] == 2

    # 运行期间下调回 1：已有运行不被强制中断（2 个仍在运行，短暂超过新上限属于允许状态）。
    service.set_quota(
        {"subject_type": "user", "subject_key": "student-a", "max_queued": 20, "max_running": 1, "daily_submissions": 1000},
        "administrator",
    )
    still_running = service.connection.execute(
        "SELECT COUNT(*) FROM compute_tasks WHERE requested_by='student-a' AND status='running'"
    ).fetchone()[0]
    assert still_running == 2
    assert service.repository.task_by_idempotency("student-a", "task-a3")["status"] == "running"
    assert service.repository.task_by_idempotency("student-a", "task-a4")["status"] == "running"

    # 新领取受新上限约束。
    submit(service, "task-a5", "student-a")
    # 尚有 2 个运行（>= 新上限 1）：A5 必须等待。
    assert concurrent_claims(["w2"])[0] is None
    # 完成一个后仍有 1 个运行（仍 >= 上限 1）：A5 继续等待。
    service.complete(task_id(service, "task-a3", "student-a"), owner_of(service, "task-a3", "student-a"), {"value": 3}, {})
    assert concurrent_claims(["w2"])[0] is None
    assert service.repository.task_by_idempotency("student-a", "task-a5")["status"] == "queued"
    # 最后一个运行任务完成、名额归零后，A5 立即可被领取，且领取后运行数不超过新上限 1。
    service.complete(task_id(service, "task-a4", "student-a"), owner_of(service, "task-a4", "student-a"), {"value": 4}, {})
    got = concurrent_claims(["w3"])[0]
    assert got == "task-a5"
    assert assert_invariant(service.connection)["student-a"] == 1
