"""批量归群 analysis.assign 的边界与不变式单元测试。

运行：docker compose exec backend python -m unittest discover -s tests -v
（本地无依赖安装时，仅需 numpy/scikit-learn 等生产依赖。）
"""
import unittest
import numpy as np
from src.services.analysis import cluster, assign, TIE_TOL

FEATURES = ['weekly_reading', 'completion']


def version(centers_z, radii, mean=None, scale=None):
    """构造最小可用的成功版本结果：恒等尺度，直接给定标准化中心与各群训练最大半径。"""
    k = len(centers_z)
    mean = [0.0] * len(FEATURES) if mean is None else mean
    scale = [1.0] * len(FEATURES) if scale is None else scale
    return {
        'features': FEATURES,
        'scaler_mean': mean,
        'scaler_scale': scale,
        'centers_z': [list(c) for c in centers_z],
        'profiles': [dict(cluster_id=i, radius_z=radii[i],
                          center_inverse={f: centers_z[i][j] for j, f in enumerate(FEATURES)})
                     for i in range(k)],
        'members': [],
        'snapshot': [],
    }


def candidate(i, code, x):
    return dict(id=i, code=code, **{f: x[j] for j, f in enumerate(FEATURES)})


class BoundaryTest(unittest.TestCase):
    def setUp(self):
        # 两个中心 (0,0) 与 (4,0)，训练最大半径均为 1.0
        self.v = version(centers_z=[[0.0, 0.0], [4.0, 0.0]], radii=[1.0, 1.0])

    def test_distance_equal_to_radius_is_assigned(self):
        # 边界：距离恰好等于训练最大半径 → 归入，不隔离
        r = assign([candidate(1, 'SL-EQ', [1.0, 0.0])], self.v)
        m = r['members'][0]
        self.assertEqual(m['status'], 'assigned')
        self.assertEqual(m['cluster_id'], 0)
        self.assertAlmostEqual(m['distance'], 1.0, places=12)
        self.assertAlmostEqual(m['radius'], 1.0, places=12)

    def test_beyond_radius_is_isolated(self):
        r = assign([candidate(2, 'SL-OUT', [1.0 + 1e-6, 0.0])], self.v)
        m = r['members'][0]
        self.assertEqual(m['status'], 'isolated')
        self.assertIsNone(m['cluster_id'])
        self.assertEqual(m['nearest_cluster_id'], 0)
        self.assertIn('超出', m['reason'])
        self.assertIn('群1', m['reason'])

    def test_exact_tie_is_isolated(self):
        # 边界：与两个群中心严格等距 → 隔离，即使未超半径
        v = version(centers_z=[[0.0, 0.0], [4.0, 0.0]], radii=[3.0, 3.0])
        r = assign([candidate(3, 'SL-TIE', [2.0, 0.0])], v)
        m = r['members'][0]
        self.assertEqual(m['status'], 'isolated')
        self.assertEqual(m['tied_cluster_ids'], [0, 1])
        self.assertIn('等距', m['reason'])

    def test_tie_tolerance_boundary(self):
        v = version(centers_z=[[0.0, 0.0], [4.0, 0.0]], radii=[3.0, 3.0])
        # 距离差 4e-10 ≤ 1e-9 → 视为等距
        near = assign([candidate(4, 'SL-NEAR', [2.0 + 2e-10, 0.0])], v)['members'][0]
        self.assertEqual(near['status'], 'isolated')
        self.assertIn('等距', near['reason'])
        # 距离差 1e-8 > 1e-9 → 归入最近群
        far = assign([candidate(5, 'SL-FAR', [2.0 + 5e-9, 0.0])], v)['members'][0]
        self.assertEqual(far['status'], 'assigned')
        self.assertEqual(far['cluster_id'], 1)

    def test_zero_distance_assigned(self):
        r = assign([candidate(6, 'SL-ZERO', [4.0, 0.0])], self.v)
        m = r['members'][0]
        self.assertEqual((m['status'], m['cluster_id'], m['distance']), ('assigned', 1, 0.0))

    def test_counts_invariant_and_unique(self):
        rows = [candidate(10 + i, f'SL-{i}', [x, y])
                for i, (x, y) in enumerate([(0.5, 0.0), (3.5, 0.0), (2.0, 0.0), (9.0, 9.0), (0.0, 0.5)])]
        r = assign(rows, self.v)
        self.assertEqual(r['assigned_count'] + r['isolated_count'], r['candidates'])
        self.assertEqual(r['candidates'], len(rows))
        self.assertEqual(len({m['id'] for m in r['members']}), len(rows))
        self.assertEqual(len(r['members']), r['assigned_count'] + r['isolated_count'])

    def test_assign_is_pure_retry_safe(self):
        rows = [candidate(20 + i, f'SL-R{i}', [float(i), 0.5]) for i in range(5)]
        self.assertEqual(assign(rows, self.v), assign(rows, self.v))

    def test_empty_candidates(self):
        r = assign([], self.v)
        self.assertEqual((r['candidates'], r['assigned_count'], r['isolated_count'], r['members']), (0, 0, 0, []))

    def test_non_finite_rejected(self):
        bad = [dict(id=30, code='SL-NAN', weekly_reading=float('nan'), completion=0.5)]
        with self.assertRaises(ValueError):
            assign(bad, self.v)


class FallbackTest(unittest.TestCase):
    def test_derive_centers_and_radii_from_legacy_version(self):
        # 旧版本没有 centers_z / radius_z：由 center_inverse 与冻结快照推导，结果一致
        v = version(centers_z=[[0.0, 0.0], [4.0, 0.0]], radii=[1.0, 1.0])
        legacy = {k: val for k, val in v.items() if k != 'centers_z'}
        legacy['profiles'] = [dict(cluster_id=p['cluster_id'], center_inverse=p['center_inverse']) for p in v['profiles']]
        legacy['snapshot'] = [candidate(1, 'SL-A', [1.0, 0.0]), candidate(2, 'SL-B', [4.5, 0.0])]
        legacy['members'] = [dict(id=1, cluster_id=0), dict(id=2, cluster_id=1)]
        rows = [candidate(9, 'SL-X', [0.25, 0.0]), candidate(10, 'SL-Y', [2.0, 0.0])]
        direct = assign(rows, v)
        derived = assign(rows, legacy)
        self.assertEqual([m['status'] for m in direct['members']], [m['status'] for m in derived['members']])
        self.assertEqual([m['cluster_id'] for m in direct['members']], [m['cluster_id'] for m in derived['members']])
        for a, b in zip(direct['members'], derived['members']):
            self.assertAlmostEqual(a['distance'], b['distance'], places=9)
            self.assertAlmostEqual(a['radius'], b['radius'], places=9)


class EndToEndTest(unittest.TestCase):
    def test_cluster_then_assign_invariants(self):
        rng = np.random.RandomState(7)
        rows = []
        for i in range(60):
            cx, cy = (5.0, 0.3) if i < 30 else (25.0, 0.8)
            rows.append(dict(id=i + 1, code=f'SL-{i + 1:03}',
                             weekly_reading=float(cx + rng.randn() * 0.5),
                             completion=float(cy + rng.randn() * 0.02),
                             night_share=0.2))
        v = cluster(rows, FEATURES, 2)
        # 训练结果携带归群所需的中心与半径
        self.assertIn('centers_z', v)
        self.assertTrue(all('radius_z' in p for p in v['profiles']))
        for p in sorted(v['profiles'], key=lambda p: p['cluster_id']):
            recomputed = (np.array([p['center_inverse'][f] for f in FEATURES]) - np.array(v['scaler_mean'])) / np.array(v['scaler_scale'])
            self.assertTrue(np.allclose(recomputed, v['centers_z'][p['cluster_id']], atol=1e-9))
        # 未参训会员归群：归入+隔离=候选，编号唯一，重算结果一致（重试不重复入群）
        new = [dict(id=100 + i, code=f'SL-N{i}', weekly_reading=float(x), completion=float(y), night_share=0.2)
               for i, (x, y) in enumerate([(5.1, 0.3), (24.9, 0.81), (999.0, 0.01)])]
        r1, r2 = assign(new, v), assign(new, v)
        self.assertEqual(r1, r2)
        self.assertEqual(r1['assigned_count'] + r1['isolated_count'], 3)
        self.assertEqual(len({m['id'] for m in r1['members']}), 3)
        far = [m for m in r1['members'] if m['code'] == 'SL-N2'][0]
        self.assertEqual(far['status'], 'isolated')
        self.assertIn('超出', far['reason'])
        near = [m for m in r1['members'] if m['code'] == 'SL-N0'][0]
        self.assertEqual(near['status'], 'assigned')
        # 半径确实等于训练样本到中心的最大标准化距离
        z = (np.array([[r[f] for f in FEATURES] for r in rows]) - np.array(v['scaler_mean'])) / np.array(v['scaler_scale'])
        label_of = {m['id']: m['cluster_id'] for m in v['members']}
        for c in range(2):
            dmax = max(float(np.linalg.norm(z[i] - np.array(v['centers_z'][c])))
                       for i, r in enumerate(rows) if label_of[r['id']] == c)
            self.assertAlmostEqual(r1['radii'][str(c)], dmax, places=9)


if __name__ == '__main__':
    unittest.main()
