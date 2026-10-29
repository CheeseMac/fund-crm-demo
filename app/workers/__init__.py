"""后台执行单元（docs/05 §2）：Scheduler 物化计划、Runner 执行 JobRun、Outbox 派发事件。

当前为单进程内嵌实现（演示环境默认开启）；代码边界已按独立进程拆分，
生产可拆为 crm-scheduler / crm-worker 分别运行，共享同一 PostgreSQL。
"""
