import json,time,traceback
from pathlib import Path
from .config.database import Base,engine,SessionLocal
from .models import UnmixRun
from .constants.status import RunStatus
from .services.run_service import queue
from .services.analysis_service import analyze_unmix
from .services.audit_service import log
Base.metadata.create_all(engine);Path("/artifacts").mkdir(exist_ok=True)
while True:
    Path("/artifacts/heartbeat").write_text(str(time.time()))
    job=queue.blpop("spectral:jobs",timeout=2)
    if not job:continue
    with SessionLocal() as db:
        run=db.get(UnmixRun,int(job[1]))
        if not run or run.status!=RunStatus.QUEUED:continue
        run.status=RunStatus.RUNNING;db.commit()
        try:
            run.result=analyze_unmix(run.input_snapshot,run.noise_sigma,run.repeats,run.seed);run.status=RunStatus.COMPLETED
            Path(f"/artifacts/run-{run.id}.json").write_text(json.dumps(run.result,ensure_ascii=False));log(db,"worker","unmix.completed",run.id)
        except Exception as e:run.status=RunStatus.FAILED;run.error=str(e);traceback.print_exc()
        db.commit()
