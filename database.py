# database.py
from sqlalchemy import create_engine, Column, String, Integer, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

SQLALCHEMY_DATABASE_URL = "sqlite:///./fb_tokens.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class FacebookUser(Base):
    __tablename__ = "facebook_users"
    id = Column(Integer, primary_key=True, index=True)
    fb_user_id = Column(String, index=True)
    long_lived_token = Column(String)
    
    # Relationship to pages
    pages = relationship("FacebookPage", back_populates="user")

class FacebookPage(Base):
    __tablename__ = "facebook_pages"
    id = Column(Integer, primary_key=True, index=True)
    page_id = Column(String, index=True)
    page_name = Column(String)
    page_access_token = Column(String, nullable=True)  # Added for page-specific token
    instagram_id = Column(String, nullable=True)
    whatsapp_id = Column(String, nullable=True)
    whatsapp_phone_number_id = Column(String, nullable=True)  # Added
    
    user_id = Column(Integer, ForeignKey("facebook_users.id"))
    user = relationship("FacebookUser", back_populates="pages")

# Create tables (Note: This won't update existing tables with new columns)
Base.metadata.create_all(bind=engine)

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()