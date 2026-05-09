from datetime import datetime
from typing import Optional, List
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import os

# Using SQLite for local persistence (easy to upgrade to PostgreSQL/ClickHouse later)
DB_PATH = os.path.expanduser("~/Desktop/semantic-log-denoiser/data/semantic_os.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

Base = declarative_base()

class AnalysisRecord(Base):
    __tablename__ = 'analyses'
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    source = Column(String)
    total_logs = Column(Integer)
    incident_summary = Column(Text)
    failure_domain = Column(String)
    anomaly_count = Column(Integer, default=0)
    
    clusters = relationship("ClusterRecord", back_populates="analysis")

class ClusterRecord(Base):
    __tablename__ = 'clusters'
    id = Column(Integer, primary_key=True)
    analysis_id = Column(Integer, ForeignKey('analyses.id'))
    cluster_id = Column(Integer)
    size = Column(Integer)
    representative_raw = Column(Text)
    representative_template = Column(Text)
    anomaly_label = Column(String)
    semantic_summary = Column(Text)
    
    analysis = relationship("AnalysisRecord", back_populates="clusters")

# Database Engine
engine = create_engine(f"sqlite:///{DB_PATH}")
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)

def save_analysis(
    source: str, 
    total_logs: int, 
    intelligence: dict, 
    clusters: list,
    anomalies: dict = None
):
    session = SessionLocal()
    try:
        # 1. Create the main Analysis record
        analysis = AnalysisRecord(
            source=source,
            total_logs=total_logs,
            incident_summary=intelligence.get("incident_summary", "N/A"),
            failure_domain=intelligence.get("failure_domain", "N/A"),
            anomaly_count=sum(1 for c in clusters if c.cluster_id == -1)
        )
        session.add(analysis)
        session.flush() # Get the ID
        
        # 2. Save each Cluster
        for i, c in enumerate(clusters):
            # Get semantic summary from intelligence if available
            summaries = intelligence.get("cluster_summaries", [])
            summary = summaries[i] if i < len(summaries) else "-"
            
            cluster_rec = ClusterRecord(
                analysis_id=analysis.id,
                cluster_id=c.cluster_id,
                size=c.size,
                representative_raw=c.representative_raw,
                representative_template=c.representative_template,
                anomaly_label="outlier" if c.cluster_id == -1 else "known",
                semantic_summary=summary
            )
            session.add(cluster_rec)
        
        session.commit()
        return analysis.id
    finally:
        session.close()
