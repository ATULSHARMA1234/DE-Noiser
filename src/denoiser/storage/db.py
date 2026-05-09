from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, JSON, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import datetime
import os

# Default to Postgres if specified, otherwise use local SQLite for the "local-first" architecture
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./semantic_os.db")

engine = create_engine(
    DATABASE_URL, 
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class Incident(Base):
    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True, index=True)
    status = Column(String, default="OPEN")
    title = Column(String)
    domain = Column(String)
    impact_score = Column(Float)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
    
    # Intelligence
    summary = Column(JSON) # List of bullet points or string
    remediation_hints = Column(JSON) # List of hints
    
    # Linked analysis context
    run_id = Column(String, nullable=True)
    source = Column(String, nullable=True)
    total_logs = Column(Integer, nullable=True)
    cluster_count = Column(Integer, nullable=True)
    
class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    
    id = Column(String, primary_key=True, index=True) # e.g. run_a1b2c3d4
    source = Column(String)
    status = Column(String)
    raw_lines = Column(Integer)
    cluster_count = Column(Integer)
    reduction_ratio = Column(Float)
    duration_sec = Column(Float)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
