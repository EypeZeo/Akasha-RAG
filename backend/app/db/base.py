"""
ORM 基类模块

提供 SQLAlchemy DeclarativeBase，所有实体模型继承自此类。
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """
    SQLAlchemy ORM 基类

    所有数据表实体类需继承此类以纳入 ORM 管理。
    """
    pass
