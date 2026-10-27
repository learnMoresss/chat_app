# 集群控制面（能力网关）：注册 worker / 插件实例、调度任务、签发文件链接。
# 单机 = 一节点集群。任务数据直连插件；启停只命令 worker-agent。
# 不绑定宿主机上其它项目的进程。

from .jobs import create_job, get_job, cancel_job
from .registry import load_gateway_config, list_capabilities_for_user

__all__ = [
    "create_job",
    "get_job",
    "cancel_job",
    "load_gateway_config",
    "list_capabilities_for_user",
]
