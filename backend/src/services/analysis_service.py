import numpy as np
from .unmix_algorithm import matrix_diagnostics
from .noise_algorithm import conditional_refits
from ..utils.fingerprint import fingerprint

def analyze_unmix(snapshot,noise_sigma=.005,repeats=80,seed=2026):
    library=snapshot['library'];members=sorted(snapshot['endmembers'],key=lambda e:e['code']);samples=sorted(snapshot['samples'],key=lambda s:s['code'])
    if not 1<=len(samples)<=6 or len(samples)*repeats>480:raise ValueError("选择1–6个样本，样本数×重复数不得超过480")
    if len({s['code'] for s in samples})!=len(samples):raise ValueError("混合样本编号重复")
    if type(seed) is not int or not 0<=seed<=2147483647:raise ValueError("根种子须为0–2147483647整数")
    wavelengths=np.asarray(library['wavelengths'],dtype=float)
    if wavelengths.ndim!=1 or not np.isfinite(wavelengths).all() or np.any(np.diff(wavelengths)<=0):raise ValueError("波长须有限且严格递增")
    A=np.array([e['spectrum'] for e in members],dtype=float).T
    _,diagnostics=matrix_diagnostics(A)
    if len(wavelengths)!=A.shape[0]:raise ValueError("波长与端元波段数不匹配")
    if not diagnostics['supported']:raise ValueError(diagnostics['reason'])
    streams=np.random.SeedSequence(seed).spawn(len(samples));results=[]
    for sample,child in zip(samples,streams):
        refits=conditional_refits(A,sample['observed'],noise_sigma,repeats,child)
        results.append({"sample_id":sample['id'],"code":sample['code'],"name":sample['name'],"observed":sample['observed'],"spawn_key":list(child.spawn_key),"high_residual":refits['point_fit']['rmse']>max(3*noise_sigma,1e-8),**refits})
    canonical={"library":library,"endmembers":members,"samples":samples}
    return {"algorithm_version":"simplex-nullspace-kkt-gaussian-v2","library":library,"endmembers":members,"samples":results,"diagnostics":diagnostics,"noise_sigma":noise_sigma,"repeats":repeats,"seed":seed,"input_snapshot":canonical,"input_hash":fingerprint({"method":"simplex-nullspace-kkt-gaussian-v2","snapshot":canonical,"noise_sigma":noise_sigma,"repeats":repeats,"seed":seed}),"assumptions":"固定校准端元的线性混合；权重非负且和为1。噪声在各波段独立同方差Gaussian，sigma由用户给定；高残差只提示失配，不是噪声或成分真实性检验。","quantile_note":"分位范围是条件扰动情景，不是配比真值的覆盖保证。","feasibility_tolerance":1e-10,"condition_limit":1e6}
