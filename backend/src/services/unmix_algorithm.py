"""Global simplex-constrained least squares by exhaustive nonempty supports (2..6 endmembers)."""
from itertools import combinations
import numpy as np
CONDITION_LIMIT=1e6
FEASIBILITY_TOL=1e-10

def matrix_diagnostics(A):
    try:A=np.asarray(A,dtype=float)
    except (TypeError,ValueError,OverflowError):raise ValueError("端元矩阵必须为有限数字")
    if A.ndim!=2 or not 2<=A.shape[1]<=6 or not 2<=A.shape[0]<=64:raise ValueError("矩阵要求2–64波段、2–6端元，列为端元")
    if not np.isfinite(A).all() or np.max(np.abs(A))>1000:raise ValueError("端元谱包含非有限或超范围数值")
    augmented=np.vstack([A,np.ones(A.shape[1])]);singular=np.linalg.svd(augmented,compute_uv=False)
    rank=int(np.sum(singular>max(augmented.shape)*np.finfo(float).eps*singular[0]));raw_condition=float(singular[0]/singular[-1]) if singular[-1]>0 else float('inf')
    condition=raw_condition if np.isfinite(raw_condition) else None
    supported=rank==A.shape[1] and raw_condition<=CONDITION_LIMIT
    reason="增广[A;1]满列秩，支持固定端元下的唯一配比解释" if supported else "增广[A;1]秩亏，配比可能非唯一，拒绝唯一成分解释" if rank<A.shape[1] else "增广[A;1]病态，条件数超过1e6，拒绝不稳定配比解释"
    return A,{"augmented_rank":rank,"condition_number":condition,"condition_limit":CONDITION_LIMIT,"supported":supported,"reason":reason}

def fit_simplex(A,y):
    A,diagnostics=matrix_diagnostics(A)
    if not diagnostics['supported']:raise ValueError(diagnostics['reason'])
    try:y=np.asarray(y,dtype=float)
    except (TypeError,ValueError,OverflowError):raise ValueError("混合谱必须为有限数字")
    if y.shape!=(A.shape[0],) or not np.isfinite(y).all() or np.max(np.abs(y))>1000:raise ValueError("混合谱长度不匹配或包含非有限/超范围数值")
    m=A.shape[1];best=None;tested=0
    for size in range(1,m+1):
        for support in combinations(range(m),size):
            S=A[:,support]
            # Equality-constrained KKT solution via an orthonormal null space.
            # Avoid forming A.T@A, which squares the condition number.
            origin=np.full(size,1/size)
            if size==1:local=origin
            else:
                _,_,vh=np.linalg.svd(np.ones((1,size)),full_matrices=True)
                basis=vh[1:].T
                try:coordinates=np.linalg.lstsq(S@basis,y-S@origin,rcond=None)[0]
                except np.linalg.LinAlgError:continue
                local=origin+basis@coordinates
            tested+=1;weights=np.zeros(m);weights[list(support)]=local
            if weights.min() < -FEASIBILITY_TOL or abs(weights.sum()-1)>1e-8:continue
            weights=np.maximum(weights,0.);weights/=weights.sum();reconstructed=A@weights;rss=float(np.sum((reconstructed-y)**2))
            if best is None or rss<best['rss']:best={"weights":weights,"rss":rss,"multiplier":float(-np.mean(2*S.T@(reconstructed-y)))}
    if best is None:raise ValueError("没有数值可行的非负和为1解")
    weights=best['weights'];gradient=2*A.T@(A@weights-y)+best['multiplier'];active=weights>1e-8
    scale=1+np.linalg.norm(A)**2+np.linalg.norm(A)*np.linalg.norm(y);tol=1e-8*scale
    active_error=float(np.max(np.abs(gradient[active]))) if active.any() else 0.
    inactive_min=float(np.min(gradient[~active])) if (~active).any() else None
    if active_error>tol or (inactive_min is not None and inactive_min < -tol):raise ValueError("完整KKT最优性校验未通过；不输出不可靠配比")
    reconstructed=A@weights;residual=y-reconstructed
    return {"weights":weights.tolist(),"reconstructed":reconstructed.tolist(),"residual":residual.tolist(),"rss":best['rss'],"rmse":float(np.sqrt(best['rss']/len(y))),"active_count":int(np.sum(weights>1e-8)),"equality_error":abs(float(weights.sum())-1),"active_stationarity_error":active_error,"inactive_slack_min":inactive_min,"kkt_tolerance":float(tol),"supports_solved":tested,"diagnostics":diagnostics}
