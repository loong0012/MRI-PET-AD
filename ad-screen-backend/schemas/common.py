"""
通用响应模型
- 前端 request.ts 约定统一响应体：{ code, message, data }
"""
from typing import Any, Generic, TypeVar, Optional

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一响应包装"""
    code: int = 200
    message: str = "success"
    data: Optional[T] = None


def ok(data: Any = None, message: str = "success") -> dict:
    """构造成功响应"""
    return {"code": 200, "message": message, "data": data}


def fail(message: str, code: int = 400) -> dict:
    """构造失败响应"""
    return {"code": code, "message": message, "data": None}


class PageResult(BaseModel, Generic[T]):
    """分页通用返回"""
    list: list[T]
    total: int
