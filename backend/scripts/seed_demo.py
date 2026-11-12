import json
import numpy as np
from src.config.database import Base,engine,SessionLocal
from src.models import User,SpectralLibrary,Endmember,MixtureSample,UnmixRun,ReplicationNote
from src.utils.security import hash_password
from src.services.run_service import create_snapshot
from src.services.analysis_service import analyze_unmix

def seed():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        for role,name in [("admin","贺青 · 材料负责人"),("analyst","苏砚 · 光谱研究员"),("viewer","林岑 · 修复阅读者")]:
            email=role+"@lengsha.demo"
            if not db.query(User).filter_by(email=email).first():db.add(User(email=email,name=name,role=role,password_hash=hash_password("Lengsha2026!")))
        waves=np.arange(400,700,25,dtype=float)
        base=np.array([.14+.5/(1+np.exp(-(waves-565)/25)),.12+.4*np.exp(-.5*((waves-525)/45)**2),.76+.03*np.cos((waves-400)/160),.12+.4*np.exp(-.5*((waves-445)/38)**2)]).T
        definitions=[('EM-01','赭石'),('EM-02','石绿'),('EM-03','白垩'),('EM-04','石青')];libraries=[]
        for idx,(code,name) in enumerate([('LS-A','校准矿物端元库'),('LS-B','近共线边界库'),('LS-C','残差失配观察库')]):
            A=base.copy()
            if idx==1:A[:,1]=A[:,0]+np.linspace(0,1e-10,len(waves))
            library=db.query(SpectralLibrary).filter_by(code=code).first()
            if not library:library=SpectralLibrary(code=code,name=name,description="12个可见光波段、4端元的合成校准谱。LS-B为近共线边界，LS-C部分样本偏离线性模型。",wavelengths=waves.tolist(),source="ls-pigment-spectra-202611.json");db.add(library);db.flush()
            for j,(ec,en) in enumerate(definitions):
                if not db.query(Endmember).filter_by(library_id=library.id,code=ec).first():db.add(Endmember(library_id=library.id,code=ec,name=en,spectrum=A[:,j].tolist()))
            rng=np.random.default_rng(119+idx)
            for i in range(1,41):
                weights=rng.dirichlet(np.ones(4)*2);observed=A@weights+rng.normal(0,.005,len(waves))
                if idx==2 and i>30:observed+=.06*np.sin(np.linspace(0,3*np.pi,len(waves)))
                sc=f"{code}-{i:03}"
                if not db.query(MixtureSample).filter_by(code=sc).first():db.add(MixtureSample(code=sc,library_id=library.id,name=f"矿物复配样本 {i:02}",observed=observed.tolist(),note="合成混合谱；真实配比未提供给算法。残差可能包含模型失配，不能只解释为测量噪声。"))
            db.flush();libraries.append(library)
        if not db.query(UnmixRun).filter_by(name="校准库 · 首批三样本解混").first():
            library=libraries[0];ids=[s.id for s in db.query(MixtureSample).filter_by(library_id=library.id).order_by(MixtureSample.code).limit(3)];snapshot=create_snapshot(db,library,ids);result=analyze_unmix(snapshot,.005,80,2026)
            run=UnmixRun(name="校准库 · 首批三样本解混",library_id=library.id,sample_ids=ids,noise_sigma=.005,repeats=80,seed=2026,input_snapshot=snapshot,status="completed",result=result,owner="苏砚 · 光谱研究员");db.add(run);db.flush()
            db.add(ReplicationNote(run_id=run.id,title="首批复配观察记录",interpretation="配比符合非负与和为1约束；分位范围条件于固定端元与给定sigma，不保证真配比覆盖。",proposal="按点估计做小样复配并重新测谱，另行评估端元校准误差和模型失配。",status="approved",owner="苏砚 · 光谱研究员",reviewer="贺青 · 材料负责人",review_note="批准研究归档；复配比例仍须独立实验验证，不把条件扰动范围当作成分真值保证。"))
        db.commit();print(json.dumps({m.__tablename__:db.query(m).count() for m in [User,SpectralLibrary,Endmember,MixtureSample,UnmixRun,ReplicationNote]},ensure_ascii=False))
if __name__=="__main__":seed()
