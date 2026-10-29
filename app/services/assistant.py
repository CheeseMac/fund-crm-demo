"""兼容层：业务逻辑已上移到 app.application.assistant，旧导入路径继续可用。"""
from app.application.assistant import interpret, confirm as confirm_actions, list_due  # noqa: F401


def confirm(db, actor, action_id):
    return confirm_actions(db, actor, action_id)
