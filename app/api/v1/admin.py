from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.db.session import get_db
from app.models.account import Account

router = APIRouter()

class AccountCreate(BaseModel):
    discord_token: str

class AccountResponse(BaseModel):
    id: int
    # discord_token: str # SECURITY: This field is commented out to prevent leaking sensitive credentials.
    is_active: bool
    last_error: str | None = None

    class Config:
        from_attributes = True

@router.post("/accounts", response_model=AccountResponse)
async def create_account(account: AccountCreate, db: AsyncSession = Depends(get_db)):
    # Check if exists
    stmt = select(Account).where(Account.discord_token == account.discord_token)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(status_code=400, detail="Token already registered")
    
    new_account = Account(discord_token=account.discord_token)
    db.add(new_account)
    await db.commit()
    await db.refresh(new_account)
    return new_account

@router.get("/accounts", response_model=list[AccountResponse])
async def list_accounts(db: AsyncSession = Depends(get_db)):
    stmt = select(Account)
    result = await db.execute(stmt)
    return result.scalars().all()