"""업로드 수신 계층: 권한, 파일 검증, ZIP 안전(경로 조작·폭탄·중첩·암호·심볼릭 링크·한글 이름), 중복, 한도, 격리"""
import io
import zipfile
from pathlib import Path

from backend.app import config
from backend.app.ingest import storage
from backend.tests.helpers import fake_pdf, make_cp949_zip, make_zip, mark_encrypted


def up(api, files, group="T-A", **data):
    payload = [("files", (name, content, "application/octet-stream")) for name, content in files]
    return api.post("/api/uploads", files=payload, data={"access_group": group, **data})


def by_name(body):
    return {j["name"]: j for j in body["jobs"]}


def test_upload_two_files_queued(login):
    r = up(login("t_lawyer_a"), [("a.pdf", fake_pdf(b"a" * 300)), ("b.pdf", fake_pdf(b"b" * 300))])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["counts"] == {"queued": 2}
    assert all(j["status"] == "queued" for j in body["jobs"])


def test_upload_requires_group_membership_and_matter_consistency(login, new_matter):
    m = new_matter()
    assert up(login("t_lawyer_b"), [("a.pdf", fake_pdf())], group="T-A").status_code == 403       # 소속 아님
    assert up(login("t_lawyer_a"), [("a.pdf", fake_pdf())], group="T-B").status_code == 403
    assert up(login("t_lawyer_a"), [("a.pdf", fake_pdf())], matter_id="MX999999").status_code == 404
    other = new_matter(group="T-B", user="t_lawyer_b")
    assert up(login("t_lawyer_a"), [("a.pdf", fake_pdf())], matter_id=other["matter_id"]).status_code in (400, 404)
    ok = up(login("t_lawyer_a"), [("ok.pdf", fake_pdf(b"z" * 400))], matter_id=m["matter_id"])
    assert ok.status_code == 201


def test_per_file_rejections_do_not_fail_the_request(login):
    files = [("virus.exe", b"MZ" + b"\0" * 100), ("fake.pdf", b"this is plain text, not a pdf"), ("empty.pdf", b""),
             ("archive.rar", b"Rar!\x1a\x07"), ("no_ext", fake_pdf()), ("good.pdf", fake_pdf(b"g" * 500))]
    r = up(login("t_lawyer_a"), files)
    assert r.status_code == 201
    jobs = by_name(r.json())
    for n in ("virus.exe", "fake.pdf", "empty.pdf", "archive.rar", "no_ext"):
        assert jobs[n]["status"] == "rejected" and jobs[n]["reason"], n
    assert "일치하지 않" in jobs["fake.pdf"]["reason"]            # 내용이 확장자와 다름
    assert jobs["good.pdf"]["status"] == "queued"


def test_zip_hostile_entries_are_rejected_individually(login):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("ok/good.pdf", fake_pdf(b"1" * 400))
        z.writestr("../evil.pdf", fake_pdf(b"2" * 400))                 # 경로 조작
        z.writestr("/abs/evil.pdf", fake_pdf(b"3" * 400))               # 절대 경로
        z.writestr("nested.zip", make_zip({"in.pdf": fake_pdf()}))      # 중첩 압축
        z.writestr("notes.txt", b"hello")                               # 지원하지 않는 형식
        z.writestr("__MACOSX/._good.pdf", b"junk")                      # OS 메타데이터(조용히 건너뜀)
        z.writestr("folder/", b"")                                      # 디렉터리
        z.writestr("secret.pdf", fake_pdf(b"4" * 400))                  # (아래에서 암호화 비트를 켠다)
        link = zipfile.ZipInfo("link.pdf"); link.external_attr = (0o120777 << 16)
        z.writestr(link, "/etc/passwd")                                 # 심볼릭 링크
    r = up(login("t_lawyer_a"), [("bundle.zip", mark_encrypted(buf.getvalue(), "secret.pdf"))])
    assert r.status_code == 201, r.text
    body = r.json()
    jobs = by_name(body)
    assert jobs["bundle.zip/ok/good.pdf"]["status"] == "queued"
    for name, needle in [("bundle.zip/../evil.pdf", "경로"), ("bundle.zip//abs/evil.pdf", "경로"), ("bundle.zip/nested.zip", "중첩"),
                         ("bundle.zip/notes.txt", "지원하지 않는"), ("bundle.zip/secret.pdf", "암호"), ("bundle.zip/link.pdf", "심볼릭")]:
        assert jobs[name]["status"] == "rejected" and needle in jobs[name]["reason"], (name, jobs[name])
    assert body["skipped_entries"] == 2
    # 이름으로 파일을 만들지 않는다: 저장소 밖에 evil.pdf가 생기지 않았다
    assert not (config.STORAGE_DIR / "evil.pdf").exists() and not (config.STORAGE_DIR.parent / "evil.pdf").exists()
    for p in storage.BLOBS.rglob("*"):
        assert p.is_dir() or len(p.name) == 64                          # 모든 저장 파일 이름은 SHA-256 해시


def test_zip_bomb_ratio_rejected(login):
    r = up(login("t_lawyer_a"), [("bomb.zip", make_zip({"big.pdf": b"%PDF-" + b"\0" * (30 * 1024 * 1024)}))])
    assert r.status_code == 201
    j = list(by_name(r.json()).values())[0]
    assert j["status"] == "rejected" and "압축률" in j["reason"]


def test_zip_limits(login, monkeypatch):
    api = login("t_lawyer_a")
    monkeypatch.setattr(config, "MAX_ZIP_ENTRIES", 3)
    r = up(api, [("many.zip", make_zip({f"{i}.pdf": fake_pdf(str(i).encode() * 50) for i in range(5)}))])
    j = list(by_name(r.json()).values())[0]
    assert j["status"] == "rejected" and "항목이 너무 많" in j["reason"]
    monkeypatch.setattr(config, "MAX_ZIP_ENTRIES", 5000)
    monkeypatch.setattr(config, "MAX_ZIP_UNCOMPRESSED_MB", 1)
    r = up(api, [("huge.zip", make_zip({"a.pdf": fake_pdf(b"x" * (2 * 1024 * 1024))}))])
    j = list(by_name(r.json()).values())[0]
    assert j["status"] == "rejected" and "용량" in j["reason"]


def test_size_and_count_limits(login, monkeypatch):
    api = login("t_lawyer_a")
    monkeypatch.setattr(config, "MAX_FILE_MB", 1)
    r = up(api, [("big.pdf", fake_pdf(b"x" * (2 * 1024 * 1024)))])
    assert list(by_name(r.json()).values())[0]["status"] == "rejected"
    monkeypatch.setattr(config, "MAX_FILES_PER_REQUEST", 2)
    assert up(api, [(f"{i}.pdf", fake_pdf()) for i in range(3)]).status_code == 400


def test_corrupt_zip_rejected_not_500(login):
    r = up(login("t_lawyer_a"), [("broken.zip", b"PK\x03\x04 this is not really a zip")])
    assert r.status_code == 201
    assert list(by_name(r.json()).values())[0]["status"] == "rejected"


def test_korean_zip_names_decoded_from_cp949(login):
    r = up(login("t_lawyer_a"), [("묶음.zip", make_cp949_zip("준비서면_제1차.pdf", fake_pdf(b"k" * 400)))])
    assert r.status_code == 201, r.text
    assert any("준비서면_제1차.pdf" in j["name"] for j in r.json()["jobs"])


def test_duplicate_detection_is_scoped_to_the_group(login):
    payload = fake_pdf(b"unique-dup-content-" * 30)
    a = login("t_lawyer_a")
    first = up(a, [("x.pdf", payload)]).json()["jobs"][0]
    second = up(a, [("copy-of-x.pdf", payload)]).json()["jobs"][0]
    assert first["status"] == "queued" and second["status"] == "duplicate" and str(first["job_id"]) in second["reason"]
    # 같은 내용을 다른 그룹이 올려도 중복으로 알려주지 않는다(다른 그룹의 존재를 드러내지 않음)
    other = up(login("t_lawyer_b"), [("x.pdf", payload)], group="T-B").json()["jobs"][0]
    assert other["status"] == "queued"


def test_append_to_existing_batch_and_batch_ownership(login):
    a = login("t_lawyer_a")
    b1 = up(a, [("p1.pdf", fake_pdf(b"p1" * 200))]).json()["batch_id"]
    r = up(a, [("p2.pdf", fake_pdf(b"p2" * 200))], batch_id=str(b1))
    assert r.status_code == 201 and r.json()["batch_id"] == b1 and r.json()["counts"]["queued"] == 2
    clerk = login("t_clerk_a")                     # 같은 그룹의 다른 사용자는 남의 묶음에 추가할 수 없다
    assert up(clerk, [("p3.pdf", fake_pdf(b"p3" * 200))], batch_id=str(b1)).status_code == 404


def test_jobs_are_isolated_between_groups(login):
    ja = up(login("t_lawyer_a"), [("iso.pdf", fake_pdf(b"iso" * 200))]).json()["jobs"][0]["job_id"]
    b = login("t_lawyer_b")
    assert b.get(f"/api/jobs/{ja}").status_code == 404
    assert ja not in {j["job_id"] for j in b.get("/api/jobs?limit=500").json()["items"]}
    assert login("t_lawyer_a").get(f"/api/jobs/{ja}").status_code == 200
    assert login("t_clerk_a").get(f"/api/jobs/{ja}").status_code == 200      # 같은 그룹 사무원은 볼 수 있다
    assert login("t_admin").get(f"/api/jobs/{ja}").status_code == 404        # 그룹 밖 관리자는 볼 수 없다
    assert b.post(f"/api/jobs/{ja}/retry").status_code == 404


def test_upload_requires_login_csrf_and_permission():
    from backend.tests.helpers import Api
    r = Api().post("/api/uploads", files=[("files", ("a.pdf", fake_pdf(), "application/pdf"))], data={"access_group": "T-A"})
    assert r.status_code == 401


def test_upload_without_csrf_rejected(login):
    a = login("t_lawyer_a")
    a.csrf = None
    assert up(a, [("a.pdf", fake_pdf())]).status_code == 403


def test_job_counts_ignore_the_status_filter(login):
    """상태 칩이 항상 전체 개수를 보여 주도록, counts는 status 필터와 무관한 범위 전체 집계다."""
    a = login("t_lawyer_a")
    r = up(a, [("ok-count.pdf", fake_pdf(b"cnt" * 200)), ("bad-count.exe", b"MZ" + b"\0" * 50)])
    batch = r.json()["batch_id"]
    only_rejected = a.get(f"/api/jobs?batch_id={batch}&status=rejected").json()
    assert [j["status"] for j in only_rejected["items"]] == ["rejected"]
    assert only_rejected["counts"] == {"queued": 1, "rejected": 1}          # 필터와 무관하게 묶음 전체 집계
    assert a.get(f"/api/jobs?batch_id={batch}&status=queued").json()["counts"] == {"queued": 1, "rejected": 1}
