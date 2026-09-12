"""
认证路由模块

提供多平台 (Douyin, Bilibili) 扫码登录、登录状态查询、退出登录等认证接口。
"""
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.account_state import record_account_state
from app.services.bilibili.client import bilibili_client
from app.services.douyin_collector import collector

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["认证"])


def _display_profile(row, nickname: str, avatar_url: str) -> tuple[str, str]:
    """Use fresh provider data when available, otherwise keep safe local cache."""
    return nickname or (row.nickname if row else ""), avatar_url or (row.avatar_url if row else "")


# ==================================================================
# 统一平台状态概览
# ==================================================================

@router.get("/platforms")
async def list_platforms_status(db: Session = Depends(get_db)):
    """获取所有支持平台的登录鉴权状态汇总"""
    dy_status, dy_msg = collector.get_status()
    bili_status = await bilibili_client.get_auth_status()
    dy_profile = collector.get_profile()
    is_dy_logged = dy_status in ("logged_in", "syncing")
    if is_dy_logged:
        from app.services.worker import worker
        worker.unblock_platform("douyin")
    dy_account = record_account_state(
        db, "douyin", active=is_dy_logged,
        auth_state_ref="playwright_user_data/state.json",
        nickname=dy_profile.get("nickname", ""),
        avatar_url=dy_profile.get("avatar_url", ""),
    )
    # Do not turn a transient validation/network failure into a durable logout.
    if bili_status.is_logged_in or bili_status.error_message in {"", "未登录"}:
        bili_account = record_account_state(
            db, "bilibili", active=bili_status.is_logged_in,
            auth_state_ref="bilibili_state.json",
            nickname=bili_status.nickname,
            avatar_url=bili_status.avatar_url,
        )
    else:
        bili_account = None
    dy_nickname, dy_avatar = _display_profile(
        dy_account, dy_profile.get("nickname", ""), dy_profile.get("avatar_url", "")
    )
    bili_nickname, bili_avatar = _display_profile(
        bili_account, bili_status.nickname, bili_status.avatar_url
    )

    return {
        "success": True,
        "platforms": [
            {
                "platform": "douyin",
                "name": "抖音",
                "is_logged_in": is_dy_logged,
                "status": dy_status,
                "nickname": dy_nickname,
                "avatar_url": dy_avatar,
                "message": dy_msg,
            },
            {
                "platform": "bilibili",
                "name": "哔哩哔哩",
                "is_logged_in": bili_status.is_logged_in,
                "status": "logged_in" if bili_status.is_logged_in else "idle",
                "account_id": bili_status.account_id,
                "nickname": bili_nickname,
                "avatar_url": bili_avatar,
                "message": bili_status.error_message,
            },
        ],
    }


# ==================================================================
# 抖音相关接口 (保持原有 /api/auth/douyin/* 完全一致)
# ==================================================================

@router.post("/douyin/login/start")
async def douyin_login_start():
    """
    启动抖音扫码登录

    后台启动 Playwright 浏览器打开抖音登录页，
    用户扫码后自动检测并保存登录态。
    """
    success, message = collector.start_login()
    return {
        "success": success,
        "message": message,
        "status": collector.status,
    }


@router.post("/douyin/qrcode/generate")
async def douyin_qrcode_generate():
    """
    启动抖音登录会话并获取内嵌渲染的 Base64 登录二维码
    """
    collector.start_login()
    qr_b64 = await collector.wait_for_qrcode(timeout=30.0)
    return {
        "success": bool(qr_b64),
        "data": {
            "qrcode_image_base64": qr_b64 or "",
            "status": collector.status,
            "expires_in": 120,
        },
        "message": collector.message,
    }


@router.post("/douyin/qrcode/refresh")
async def douyin_qrcode_refresh():
    """
    刷新抖音登录二维码
    """
    qr_b64 = collector.refresh_qrcode()
    return {
        "success": bool(qr_b64),
        "data": {
            "qrcode_image_base64": qr_b64 or "",
            "status": collector.status,
        },
        "message": "二维码已刷新" if qr_b64 else "刷新失败，请稍候重试",
    }


@router.post("/douyin/window/show")
async def douyin_window_show():
    """
    将后台隐形/最小化的浏览器窗口恢复至桌面，便于用户完成滑块验证等交互
    """
    ok = collector.show_browser_window()
    return {
        "success": ok,
        "message": "已恢复显示浏览器窗口" if ok else "未找到可恢复的浏览器窗口",
    }


@router.get("/douyin/login/status")
async def douyin_login_status():
    """查询抖音登录状态，附带最新二维码 Base64（如就绪）"""
    status, message = collector.get_status()
    return {
        "status": status,
        "message": message,
        "qrcode_image_base64": collector.get_qrcode() or "",
    }


@router.post("/douyin/login/cancel")
async def douyin_login_cancel():
    """取消正在进行的抖音扫码登录并释放浏览器资源"""
    success, message = collector.cancel_login()
    return {
        "success": success,
        "message": message,
        "status": collector.status,
    }


@router.post("/douyin/logout")
async def douyin_logout(db: Session = Depends(get_db)):
    """立即停止该平台新入库工作并清除本地登录态。"""
    from app.services.worker import worker
    worker.block_platform("douyin")
    success, message = collector.logout()
    record_account_state(db, "douyin", active=False, auth_state_ref="")
    return {
        "success": success,
        "message": message,
        "status": collector.status,
    }


# ==================================================================
# Bilibili 相关接口 (/api/auth/bilibili/*)
# ==================================================================

@router.post("/bilibili/qrcode/generate")
async def bilibili_qrcode_generate():
    """申请 B 站登录二维码及 Base64 渲染图片"""
    try:
        qr = await bilibili_client.generate_qrcode()
        return {
            "success": True,
            "data": {
                "qrcode_key": qr.qrcode_key,
                "qrcode_url": qr.qrcode_url,
                "qrcode_image_base64": qr.qrcode_image_base64,
                "expires_in": qr.expires_in,
            },
        }
    except Exception as exc:
        logger.exception("生成 B站 登录二维码失败")
        return {"success": False, "message": str(exc)}


@router.get("/bilibili/qrcode/poll")
async def bilibili_qrcode_poll(
    qrcode_key: str = Query(..., description="二维码唯一 Key"),
    db: Session = Depends(get_db),
):
    """轮询 B 站二维码扫码登录状态"""
    try:
        result = await bilibili_client.poll_qrcode_status(qrcode_key)
        if result.status == "confirmed":
            from app.services.worker import worker
            worker.unblock_platform("bilibili")
            record_account_state(
                db, "bilibili", active=True, auth_state_ref="bilibili_state.json",
                nickname=result.nickname, avatar_url=result.avatar_url,
            )
        return {
            "success": True,
            "status": result.status,
            "message": result.message,
            "account_id": result.account_id,
            "nickname": result.nickname,
            "avatar_url": result.avatar_url,
        }
    except Exception as exc:
        logger.exception("轮询 B站 二维码状态失败")
        return {"success": False, "status": "error", "message": str(exc)}


@router.get("/bilibili/status")
async def bilibili_status():
    """查询 B 站登录凭据有效状态"""
    try:
        status = await bilibili_client.get_auth_status()
        return {
            "success": True,
            "is_logged_in": status.is_logged_in,
            "account_id": status.account_id,
            "nickname": status.nickname,
            "avatar_url": status.avatar_url,
            "message": status.error_message,
        }
    except Exception as exc:
        return {"success": False, "is_logged_in": False, "message": str(exc)}


@router.post("/bilibili/logout")
async def bilibili_logout(db: Session = Depends(get_db)):
    """立即停止该平台新入库工作并清除本地 Cookie 状态。"""
    try:
        from app.services.worker import worker
        worker.block_platform("bilibili")
        bilibili_client.clear_state()
        record_account_state(db, "bilibili", active=False, auth_state_ref="")
        return {"success": True, "message": "已退出 B站 登录"}
    except Exception as exc:
        return {"success": False, "message": str(exc)}


@router.post("/logout-all")
async def logout_all(db: Session = Depends(get_db)):
    """Clear every supported local session after the explicit UI confirmation."""
    from app.services.worker import worker

    worker.block_platform("douyin")
    worker.block_platform("bilibili")
    dy_ok, dy_message = collector.logout()
    try:
        bilibili_client.clear_state()
        bili_ok, bili_message = True, "已退出 B站 登录"
    except Exception as exc:
        bili_ok, bili_message = False, str(exc)
    record_account_state(db, "douyin", active=False, auth_state_ref="")
    record_account_state(db, "bilibili", active=False, auth_state_ref="")
    return {
        "success": dy_ok and bili_ok,
        "platforms": {
            "douyin": {"success": dy_ok, "message": dy_message},
            "bilibili": {"success": bili_ok, "message": bili_message},
        },
    }
