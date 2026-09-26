from __future__ import annotations


def create_resident(client):
    response = client.post(
        "/residents",
        json={"name": "张三", "id_card": "110101199001011234", "gender": "男", "birth_date": "1990-01-01", "phone": "13800000000", "address": "幸福路一号", "village": "幸福村"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_department(client):
    response = client.post("/departments", json={"name": "综合服务中心", "manager": "李主任", "phone": "010-12345678"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_resident_and_affair_flow(client):
    resident_id = create_resident(client)
    department_id = create_department(client)
    affair = client.post("/affairs", json={"title": "社保材料补录", "category": "社保", "applicant_id": resident_id, "description": "补录材料"})
    assert affair.status_code == 201
    affair_id = affair.json()["id"]
    processing = client.put(f"/affairs/{affair_id}/process", json={"status": "办理中", "department_id": department_id, "handler": "王经办"})
    assert processing.status_code == 200
    completed = client.put(f"/affairs/{affair_id}/process", json={"status": "已办结", "department_id": department_id, "handler": "王经办", "result": "补录完成"})
    assert completed.status_code == 200
    detail = client.get(f"/affairs/{affair_id}")
    assert detail.json()["status"] == "已办结"


def test_petition_workflow(client):
    department_id = create_department(client)
    petition = client.post("/petitions", json={"type": "意见建议", "target": "村道照明", "content": "建议增设照明", "contact": "13800000000"})
    assert petition.status_code == 201
    petition_id = petition.json()["id"]
    assert client.post(f"/petitions/{petition_id}/receive").status_code == 200
    assert client.post(f"/petitions/{petition_id}/assign", json={"department_id": department_id, "deadline_days": 5}).status_code == 200
    assert client.post(f"/petitions/{petition_id}/process", json={"result": "已安排施工"}).status_code == 200
    assert client.post(f"/petitions/{petition_id}/review", json={"passed": True, "review_opinion": "通过"}).status_code == 200
    detail = client.get(f"/petitions/{petition_id}").json()
    assert detail["status"] == "已办结"
    assert [item["action"] for item in detail["flow_records"]][-1] == "审核通过"


def test_announcement_sorting(client):
    for title, pinned in (("普通通知", False), ("置顶政策", True)):
        response = client.post("/announcements", json={"title": title, "content": "正文", "category": "通知", "publisher": "办公室", "is_pinned": pinned})
        assert response.status_code == 201
    rows = client.get("/announcements").json()["data"]
    assert rows[0]["title"] == "置顶政策"
