import os
os.environ["DATABASE_URL"] = "postgresql://postgres:postgres@localhost:5432/semanticos"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from denoiser.storage.db import User, Base
from denoiser.api.auth import get_password_hash

engine = create_engine(os.environ["DATABASE_URL"])
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
session = Session()

existing = session.query(User).filter_by(email="test@example.com").first()
if not existing:
    u = User(email="test@example.com", hashed_password=get_password_hash("password"), role="ADMIN")
    session.add(u)
    session.commit()
    print("User created!")
else:
    print("User already exists.")
