import os
DATABASE_URL=os.environ.get("DATABASE_URL","mysql+pymysql://spectral:spectral@db/spectral?charset=utf8mb4")
REDIS_URL=os.environ.get("REDIS_URL","redis://redis:6379/0")
JWT_SECRET=os.environ.get("JWT_SECRET","local-spectral-mixture-change-this-key")
SEED_DEMO=os.environ.get("SEED_DEMO","true").lower()=="true"
