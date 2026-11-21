import json,hashlib
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
FEATURES=['weekly_reading','completion','night_share']
SEED=3107
TIE_TOL=1e-9
def cluster(rows,features,k):
 if len(features)<2 or len(set(features))!=len(features) or not set(features)<=set(FEATURES):raise ValueError('选择2–3个不同的有效特征')
 if k<2 or k>6:raise ValueError('分群数仅支持2–6')
 if len(rows)<=k:raise ValueError('样本数必须大于分群数，补充会员档案或降低k')
 x=np.array([[r[f] for f in features] for r in rows],dtype=float)
 if not np.isfinite(x).all():raise ValueError('特征包含缺失或非有限值，请先修订档案')
 if np.any(np.ptp(x,axis=0)<=1e-12):raise ValueError('所选特征中存在常量列，请移除该特征或补充有差异的样本')
 if len(np.unique(x,axis=0))<k:raise ValueError('不同特征点少于k，不能形成指定数量的群组')
 scaler=StandardScaler();z=scaler.fit_transform(x);model=KMeans(n_clusters=k,random_state=SEED,n_init=20,algorithm='lloyd');labels=model.fit_predict(z)
 if len(set(labels))!=k:raise ValueError('实际群数小于k，请检查重复点或降低k')
 centers=scaler.inverse_transform(model.cluster_centers_)
 order=sorted(range(k),key=lambda i:tuple(centers[i]));mapping={old:new for new,old in enumerate(order)}
 centers_z=[model.cluster_centers_[old].tolist() for old in order]
 memberships=[dict(id=r['id'],code=r['code'],cluster_id=mapping[int(label)],**{f:r[f] for f in FEATURES},x=float(point[0]),y=float(point[1])) for r,label,point in zip(rows,labels,z)]
 profiles=[]
 for old in order:
  members=[r for r in memberships if r['cluster_id']==mapping[old]]
  pts=z[labels==old]
  radius=float(np.max(np.linalg.norm(pts-model.cluster_centers_[old],axis=1)))
  profiles.append(dict(cluster_id=mapping[old],size=len(members),portrait={f:float(np.mean([r[f] for r in members])) for f in FEATURES},center_inverse={f:float(v) for f,v in zip(features,centers[old])},radius_z=radius,member_codes=[r['code'] for r in members]))
 return dict(n=len(rows),k=k,features=features,profiles=profiles,members=memberships,silhouette=float(silhouette_score(z,labels)),inertia=float(model.inertia_),scaler_mean=scaler.mean_.tolist(),scaler_scale=scaler.scale_.tolist(),centers_z=centers_z,seed=SEED,checksum=hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),method='StandardScaler总体均值/标准差→KMeans欧氏距离，n_init=20、seed=3107；群号按原始中心排序，便于同一版本复核；轮廓系数用标准化空间。训练最大半径为群内样本到中心的最大标准化距离，供批量归群沿用。分群是行为相似性，不是会员价值或因果判断。')
def assign(rows,version):
 features=version['features'];mean=np.array(version['scaler_mean'],dtype=float);scale=np.array(version['scaler_scale'],dtype=float)
 profiles=sorted(version['profiles'],key=lambda p:p['cluster_id']);k=len(profiles)
 if version.get('centers_z'):centers=np.array(version['centers_z'],dtype=float)
 else:centers=(np.array([[p['center_inverse'][f] for f in features] for p in profiles],dtype=float)-mean)/scale
 if all('radius_z' in p for p in profiles):radii=np.array([float(p['radius_z']) for p in profiles])
 else:
  radii=np.zeros(k);label_of={m['id']:m['cluster_id'] for m in version['members']}
  for r in version['snapshot']:
   point=(np.array([r[f] for f in features],dtype=float)-mean)/scale
   c=label_of[r['id']];radii[c]=max(radii[c],float(np.linalg.norm(point-centers[c])))
 base=dict(features=features,scaler_mean=mean.tolist(),scaler_scale=scale.tolist(),centers_z=centers.tolist(),radii={str(p['cluster_id']):float(radii[p['cluster_id']]) for p in profiles},tie_tolerance=TIE_TOL,method='沿用成功版本的特征、StandardScaler尺度与标准化中心，按欧氏距离归入最近群；与多个群中心等距（距离差≤1e-9）或距离超出该群训练最大半径的会员隔离并说明。归群描述行为相似性，不代表会员价值。')
 if not rows:return dict(base,candidates=0,assigned_count=0,isolated_count=0,members=[])
 x=np.array([[r[f] for f in features] for r in rows],dtype=float)
 if not np.isfinite(x).all():raise ValueError('候选会员特征包含缺失或非有限值，请先修订档案')
 z=(x-mean)/scale;dist=np.linalg.norm(z[:,None,:]-centers[None,:,:],axis=2)
 members=[]
 for r,drow in zip(rows,dist):
  nearest=int(np.argmin(drow));dmin=float(drow[nearest]);radius=float(radii[nearest])
  tied=[c for c in range(k) if drow[c]<=dmin+TIE_TOL]
  item=dict(id=r['id'],code=r['code'],distance=dmin,nearest_cluster_id=nearest,radius=radius)
  if len(tied)>1:members.append(dict(item,status='isolated',cluster_id=None,tied_cluster_ids=tied,reason='与'+'、'.join('群'+str(c+1) for c in tied)+'中心等距（距离差≤1e-9），无法唯一归入'))
  elif dmin>radius:members.append(dict(item,status='isolated',cluster_id=None,tied_cluster_ids=[],reason=f'距群{nearest+1}中心{dmin:.4f}，超出该群训练最大半径{radius:.4f}'))
  else:members.append(dict(item,status='assigned',cluster_id=nearest,tied_cluster_ids=[],reason=''))
 assigned=sum(1 for m in members if m['status']=='assigned')
 return dict(base,candidates=len(rows),assigned_count=assigned,isolated_count=len(rows)-assigned,members=members)
