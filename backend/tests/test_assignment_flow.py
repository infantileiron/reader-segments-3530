"""批量归群 API 全流程集成测试：生命周期、失效、重试、签核竞争。

运行：docker compose exec backend python -m unittest tests.test_assignment_flow -v
本地无 PostgreSQL/Redis 时自动退化为 SQLite + fakeredis（行级锁路径仅在
PostgreSQL 上启用，本测试验证的是可观察的先后语义与一致性）：
    pip install fakeredis httpx && python -m unittest tests.test_assignment_flow -v
"""
import os
import sys
import json
import pathlib
import tempfile
import threading
import unittest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
TMP = pathlib.Path(tempfile.mkdtemp(prefix='assignment-flow-'))
os.environ['DATABASE_URL'] = os.environ.get('TEST_DATABASE_URL', 'sqlite:///' + str(TMP / 'flow.db'))
os.environ['REDIS_URL'] = 'redis://integration-test/0'
os.environ['JWT_SECRET'] = 'flow-test-secret'
os.environ['SEED_DEMO'] = 'false'

try:
    import fakeredis
    import redis
    _FAKE = fakeredis.FakeStrictRedis()
    redis.Redis.from_url = lambda *a, **k: _FAKE
except ImportError:  # pragma: no cover
    fakeredis = None

from fastapi.testclient import TestClient  # noqa: E402
from src.config.database import Base, engine, Session  # noqa: E402
from src.models import entities  # noqa: E402
from src.models.entities import User  # noqa: E402
from src.utils.security import hash_password  # noqa: E402
import src.worker as worker  # noqa: E402

ARTIFACTS = TMP / 'artifacts'
ARTIFACTS.mkdir(exist_ok=True)
_REAL_PATH = worker.Path
worker.Path = lambda p: _REAL_PATH(str(p).replace('/artifacts/', str(ARTIFACTS) + '/'))
from src.main import app  # noqa: E402

READER = lambda code, w, c, n=0.2: {'code': code, 'weekly_reading': w, 'completion': c, 'night_share': n, 'note': '集成测试'}


@unittest.skipUnless(fakeredis, '需要开发依赖 fakeredis 与 httpx')
class FlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(engine)
        with Session() as s:
            for role in ('admin', 'analyst', 'viewer'):
                s.add(User(email=role + '@demo.local', role=role, password=hash_password('Demo123!')))
            s.commit()
        cls.api = TestClient(app)
        cls.h = {}
        for role in ('admin', 'analyst', 'viewer'):
            r = cls.api.post('/auth/login', json={'email': role + '@demo.local', 'password': 'Demo123!'})
            assert r.status_code == 200, r.text
            cls.h[role] = {'Authorization': 'Bearer ' + r.json()['token']}
        # 30 位会员，两个清晰簇；再训练 k=2 版本
        for i in range(30):
            w, c = (5.0 + (i % 5) * 0.1, 0.30) if i < 15 else (25.0 + (i % 5) * 0.1, 0.80)
            r = cls.api.post('/readers', json=READER(f'SL-{i + 1:03}', w, c), headers=cls.h['analyst'])
            assert r.status_code == 200, r.text
        r = cls.api.post('/segments', json={'name': '集成测试分群', 'k': 2,
                                            'features': ['weekly_reading', 'completion']}, headers=cls.h['analyst'])
        assert r.status_code == 200, r.text
        cls.segment_id = r.json()['id']
        worker.run_segment(cls.segment_id)
        seg = cls.api.get(f'/segments/{cls.segment_id}', headers=cls.h['viewer']).json()
        assert seg['state'] == 'done', seg
        cls.cluster_of_low = None
        for p in seg['result']['profiles']:
            if p['portrait']['weekly_reading'] < 15:
                cls.cluster_of_low = p['cluster_id']
        assert cls.cluster_of_low is not None

    def tearDown(self):
        for key in ('reader:segments', 'reader:assignments'):
            _FAKE.delete(key)

    def assignment(self, i):
        r = self.api.get(f'/assignments/{i}', headers=self.h['viewer'])
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def run_done(self, i):
        worker.run_assignment(i)
        a = self.assignment(i)
        self.assertEqual(a['state'], 'done', a)
        self.assertTrue(a['valid'], a)
        return a

    def add_reader(self, code, w, c):
        r = self.api.post('/readers', json=READER(code, w, c), headers=self.h['analyst'])
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['id']

    def edit_reader(self, i, code, w, c):
        r = self.api.put(f'/readers/{i}', json=READER(code, w, c), headers=self.h['analyst'])
        self.assertEqual(r.status_code, 200, r.text)

    def new_assignment(self):
        r = self.api.post('/assignments', json={'segment_id': self.segment_id}, headers=self.h['analyst'])
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['id']

    def test_01_requires_candidates_and_done_segment(self):
        r = self.api.post('/assignments', json={'segment_id': self.segment_id}, headers=self.h['analyst'])
        self.assertEqual(r.status_code, 422, r.text)  # 没有未参训会员
        r = self.api.post('/assignments', json={'segment_id': 9999}, headers=self.h['analyst'])
        self.assertEqual(r.status_code, 404, r.text)

    def test_02_lifecycle_counts_and_isolation(self):
        self.add_reader('SL-N1', 5.05, 0.30)   # 贴近低频簇 → 归入
        self.add_reader('SL-N2', 25.05, 0.80)  # 贴近高频簇 → 归入
        self.add_reader('SL-N3', 999.0, 0.01)  # 远离所有群 → 隔离（超出半径）
        aid = self.new_assignment()
        a = self.assignment(aid)
        self.assertEqual((a['state'], a['candidates']), ('pending', 3), a)
        a = self.run_done(aid)
        res = a['result']
        self.assertEqual(res['candidates'], 3)
        self.assertEqual(res['assigned_count'] + res['isolated_count'], res['candidates'])
        self.assertEqual(len({m['id'] for m in res['members']}), 3)
        by_code = {m['code']: m for m in res['members']}
        self.assertEqual(by_code['SL-N1']['status'], 'assigned')
        self.assertEqual(by_code['SL-N1']['cluster_id'], self.cluster_of_low)
        self.assertEqual(by_code['SL-N3']['status'], 'isolated')
        self.assertIn('超出', by_code['SL-N3']['reason'])
        self.assertGreater(by_code['SL-N3']['distance'], by_code['SL-N3']['radius'])
        # 重复投递同一任务（worker 重启恢复）→ 幂等，不重复入群
        worker.run_assignment(aid)
        self.assertEqual(self.assignment(aid)['result'], res)
        # 有效结果不允许重试
        r = self.api.post(f'/assignments/{aid}/retry', headers=self.h['analyst'])
        self.assertEqual(r.status_code, 409, r.text)
        # 导出
        r = self.api.get(f'/assignments/{aid}/export', headers=self.h['viewer'])
        self.assertEqual(r.status_code, 200, r.text)
        self.__class__.aid = aid

    def test_03_invalidation_blocks_draft_and_approve(self):
        aid = self.__class__.aid
        # 有效归群可起草：成员 = 训练冻结成员 + 归入成员
        r = self.api.post('/outreaches', json={'segment_id': self.segment_id, 'cluster_id': self.cluster_of_low,
                                               'title': '低频读者轻阅读试点', 'note': '每周一次短篇精选，站内触达。',
                                               'assignment_id': aid}, headers=self.h['analyst'])
        self.assertEqual(r.status_code, 200, r.text)
        o = r.json()
        self.assertEqual(o['assignment_revision'], self.assignment(aid)['source_revision'])
        self.assertIn('SL-N1', o['assigned_members'])
        self.assertIn('SL-N1', o['members'])
        oid = o['id']
        self.__class__.oid = oid
        # 源数据修订 → 归群失效，草案不能签核
        self.edit_reader(1, 'SL-001', 5.2, 0.31)
        a = self.assignment(aid)
        self.assertFalse(a['valid'], a)
        self.assertGreater(a['current_revision'], a['source_revision'])
        r = self.api.post(f'/outreaches/{oid}/approve', headers=self.h['admin'])
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn('失效', r.json()['detail'])
        self.assertEqual(self.api.get('/outreaches', headers=self.h['viewer']).json()[0]['status'], 'draft')
        # 失效归群也不能再起草新方案
        r = self.api.post('/outreaches', json={'segment_id': self.segment_id, 'cluster_id': self.cluster_of_low,
                                               'title': '失效归群不应可起草', 'note': '每周一次短篇精选，站内触达。',
                                               'assignment_id': aid}, headers=self.h['analyst'])
        self.assertEqual(r.status_code, 409, r.text)

    def test_04_retry_recomputes_wholesale(self):
        aid = self.__class__.aid
        before = self.assignment(aid)['source_revision']
        r = self.api.post(f'/assignments/{aid}/retry', headers=self.h['analyst'])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['state'], 'pending')
        self.assertGreater(r.json()['source_revision'], before)
        a = self.run_done(aid)
        res = a['result']
        self.assertEqual(res['assigned_count'] + res['isolated_count'], res['candidates'])
        self.assertEqual(len({m['id'] for m in res['members']}), res['candidates'])
        # 重试后旧草案仍按其冻结的数据版本判定 → 不能签核
        r = self.api.post(f"/outreaches/{self.__class__.oid}/approve", headers=self.h['admin'])
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn('失效', r.json()['detail'])

    def test_05_revision_during_compute_not_published(self):
        self.add_reader('SL-N4', 5.15, 0.30)
        aid = self.new_assignment()
        self.edit_reader(2, 'SL-002', 5.3, 0.32)  # 计算前源数据已变 → 发布守卫必须拦截
        worker.run_assignment(aid)
        a = self.assignment(aid)
        self.assertEqual(a['state'], 'failed', a)
        self.assertIn('计算期间源数据已修订', a['error'])
        self.assertIsNone(a['result'])
        # 重试按当前数据版本重新归群并成功
        r = self.api.post(f'/assignments/{aid}/retry', headers=self.h['analyst'])
        self.assertEqual(r.status_code, 200, r.text)
        a = self.run_done(aid)
        self.assertEqual(a['candidates'], 4)
        self.assertEqual(a['result']['assigned_count'] + a['result']['isolated_count'], 4)
        self.__class__.aid2 = aid

    def test_06_signed_list_frozen_after_invalidation(self):
        aid = self.__class__.aid2
        r = self.api.post('/outreaches', json={'segment_id': self.segment_id, 'cluster_id': self.cluster_of_low,
                                               'title': '签核后冻结验证', 'note': '每周一次短篇精选，站内触达。',
                                               'assignment_id': aid}, headers=self.h['analyst'])
        self.assertEqual(r.status_code, 200, r.text)
        oid = r.json()['id']
        r = self.api.post(f'/outreaches/{oid}/approve', headers=self.h['admin'])
        self.assertEqual(r.status_code, 200, r.text)
        members = list(r.json()['members'])
        # 签核后源数据再修订：名单保持冻结，状态不变
        self.edit_reader(3, 'SL-003', 5.4, 0.33)
        o = [x for x in self.api.get('/outreaches', headers=self.h['viewer']).json() if x['id'] == oid][0]
        self.assertEqual(o['status'], 'approved')
        self.assertEqual(o['members'], members)
        # 重复签核 → 409
        r = self.api.post(f'/outreaches/{oid}/approve', headers=self.h['admin'])
        self.assertEqual(r.status_code, 409, r.text)

    def test_07_approve_and_revision_race(self):
        # 并发签核与修订必须有明确先后：要么签核先于修订生效（approved 且冻结），
        # 要么修订先提交（签核 409，保持 draft），不允许 5xx 或中间态。
        for round_no in range(5):
            self.edit_reader(4, 'SL-004', 5.5 + round_no * 0.01, 0.34)
            aid = self.__class__.aid2
            r = self.api.post(f'/assignments/{aid}/retry', headers=self.h['analyst'])
            self.assertEqual(r.status_code, 200, r.text)
            self.run_done(aid)
            r = self.api.post('/outreaches', json={'segment_id': self.segment_id, 'cluster_id': self.cluster_of_low,
                                                   'title': f'竞争轮次{round_no}', 'note': '每周一次短篇精选，站内触达。',
                                                   'assignment_id': aid}, headers=self.h['analyst'])
            self.assertEqual(r.status_code, 200, r.text)
            oid = r.json()['id']
            results = {}

            def do_approve():
                results['approve'] = self.api.post(f'/outreaches/{oid}/approve', headers=self.h['admin'])

            def do_edit():
                results['edit'] = self.api.put('/readers/5', json=READER('SL-005', 5.6 + round_no * 0.01, 0.35),
                                               headers=self.h['analyst'])

            t1, t2 = threading.Thread(target=do_approve), threading.Thread(target=do_edit)
            t1.start(); t2.start(); t1.join(); t2.join()
            self.assertEqual(results['edit'].status_code, 200, results['edit'].text)
            self.assertIn(results['approve'].status_code, (200, 409), results['approve'].text)
            o = [x for x in self.api.get('/outreaches', headers=self.h['viewer']).json() if x['id'] == oid][0]
            if results['approve'].status_code == 200:
                self.assertEqual(o['status'], 'approved')  # 签核先提交：修订随后生效，名单冻结
            else:
                self.assertEqual(o['status'], 'draft')     # 修订先提交：签核被失效拦截
                self.assertIn('失效', results['approve'].json()['detail'])

    def test_08_permissions(self):
        for role in ('viewer',):
            r = self.api.post('/assignments', json={'segment_id': self.segment_id}, headers=self.h[role])
            self.assertEqual(r.status_code, 403, r.text)
            r = self.api.post(f"/assignments/{self.__class__.aid2}/retry", headers=self.h[role])
            self.assertEqual(r.status_code, 403, r.text)
        r = self.api.post(f"/outreaches/{self.__class__.aid2}/approve", headers=self.h['analyst'])
        self.assertEqual(r.status_code, 403, r.text)  # 签核是负责人独占
        r = self.api.get('/assignments', headers=self.h['viewer'])
        self.assertEqual(r.status_code, 200, r.text)


if __name__ == '__main__':
    unittest.main()
