## GPU server 与实验执行（Brain++）

本项目的 GPU 后端为 Brain++ H 集群（`gpu: brainpp`）。当任务需要 GPU server 或执行 GPU 工作负载时，必须先读取并使用 [brainpp-rjob skill](.agents/skills/brainpp-rjob/SKILL.md)。namespace、charged-group、image、GPFS mount 和资源规格以该 skill 的本项目配置为准，用户明确指定的覆盖项优先；不要自行改卡数或切换后端。

此约定适用于所有研究阶段，包括 `idea-discovery` / `idea-creator` 的 pilot、sanity check、正式训练、GPU 评估与推理、消融实验、评审或 rebuttal 补实验，以及需要 GPU 的验收、benchmark、profiling 和环境诊断。纯 CPU 检查、文献检索和已有结果分析可在本地执行。

- 使用前读取 skill 的 [rjob 操作说明](.agents/skills/brainpp-rjob/references/rjob-operations.md)；进行研究迭代时再读取 [实验循环说明](.agents/skills/brainpp-rjob/references/experiment-loop.md)。
- 所有 GPU 远程命令写入 GPFS 共享目录中的 Bash 启动脚本，通过 `rjob submit ... -- bash <共享路径/run.sh>` 执行。不要使用 SSH、screen/tmux、nohup 或保活任务替代 rjob 主命令。
- 用本次提交返回的完整 metadata name 查询、停止和清理任务。通过 `rjob get` 查询状态；分析只读取共享 mount 中已落盘的日志和结果。提交前落实整个脚本的 stdout/stderr 重定向、指标保存和运行时间上限。
- 确认任务总体状态为 `Succeeded` 后，核对产物、保存最终状态，再用 `rjob delete` 清理本次任务；失败或超时按 skill 保存证据并处理。不要把提交成功或 `Succeeded` 当成有效实验结果。
- 每轮使用独立源码快照和输出目录，不修改已提交任务的代码、脚本或依赖环境。真实 conda 路径及 CUDA/torch 能力必须针对实际 worker 验证。
- 在用户已授权的范围和预算内执行，默认串行。pilot 的候选数量不等于并发任务数；按实际申请卡数计算 GPU-hours，失败尝试也计入预算。子 skill 继承剩余预算，不能重新获得一份预算。

当现有 ARIS skills 写有 SSH 部署、固定 GPU 编号、单卡 pilot、默认并行或按任务数转交 SSH `experiment-queue` 的步骤时，在本项目中按上述 Brain++ 执行约定适配，保留原有研究目标、评价协议和产物要求。本地没有 GPU 不代表集群不可用；先检查配置的 rjob 后端。此配置本身不授权额外实验或扩大资源预算。
