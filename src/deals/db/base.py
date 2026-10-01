from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from deals.config import settings

engine = create_async_engine(settings.DATABASE_URL, pool_size=5, max_overflow=5, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
