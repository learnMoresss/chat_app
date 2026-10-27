# 集群数据面：本进程是 worker-agent，只管本机插件生命周期与 GPU 探活。
# 单机 = 一节点集群（1 个网关 + 1 个本机 agent）。
# 不绑宿主机上其它项目的进程；只启动本仓库 plugins/*/plugin.yaml 白名单命令。

__version__ = "0.1.0"
