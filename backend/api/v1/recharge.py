"""Recharge API routes - /api/v1/recharge/*"""
from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import desc, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.deps import get_current_user
from models.recharge_package import RechargePackage
from schemas.common import ResponseWrapper

router = APIRouter(prefix="/api/v1/recharge", tags=["recharge"])


@router.get("/packages")
async def list_packages(db: AsyncSession = Depends(get_db)):
    """List available recharge packages."""
    result = await db.execute(
        select(RechargePackage)
        .where(RechargePackage.enabled == True, RechargePackage.deleted == False)
        .order_by(RechargePackage.sort_order)
    )
    packages = result.scalars().all()
    return ResponseWrapper(code=200, message="success", data=[
        {"id": p.id, "packageName": p.package_name, "packagePrice": p.package_price,
         "packageDesc": p.package_desc, "packageBenefit": p.package_benefit,
         "llmToken": p.llm_token, "embeddingToken": p.embedding_token,
         "sortOrder": p.sort_order}
        for p in packages
    ]).model_dump()


@router.get("/orders")
async def list_orders(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    status: str | None = Query(None),
):
    """List user's recharge orders."""
    from models.recharge_order import RechargeOrder
    conditions = [RechargeOrder.user_id == user["user_id"]]
    if status:
        conditions.append(RechargeOrder.status == status)
    result = await db.execute(
        select(RechargeOrder).where(*conditions).order_by(desc(RechargeOrder.created_at))
    )
    orders = result.scalars().all()
    return ResponseWrapper(code=200, message="success", data=[
        {"id": o.id, "tradeNo": o.trade_no, "amount": o.amount,
         "llmToken": o.llm_token, "embeddingToken": o.embedding_token,
         "status": o.status, "payTime": str(o.pay_time) if o.pay_time else None,
         "createdAt": str(o.created_at) if o.created_at else None}
        for o in orders
    ]).model_dump()
