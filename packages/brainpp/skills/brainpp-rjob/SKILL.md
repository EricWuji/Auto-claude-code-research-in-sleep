---
name: brainpp-rjob
description: 在本项目的 Brain++ H 集群中使用 rjob 提交、调试 GPU 任务，读取共享存储结果，并在用户指定范围内进行修改代码、运行实验和分析结果的研究迭代。用于 rjob 或 GPU 实验请求，不用于其他集群的调度或普通本地运行。
---

# Brain++ rjob GPU 实验

本项目选择基础镜像提供系统环境，GPFS 提供代码、conda 环境、数据和结果。优先在共享存储修改代码，编写启动 Bash 脚本，通过 `rjob submit ... -- bash /共享路径/run.sh` 运行实验。

## 本项目配置

| 项目 | 配置 |
|---|---|
| namespace | `ailab-llmagent` |
| GPU charged-group | `llmagent_gpu` |
| 私有机器 | `--private-machine=group` |
| image | `registry.h.pjlab.org.cn/ailab/ml-base:22.04-pjlab-20251117` |
| GPFS mount | `gpfs://gpfs1/songdemin:/mnt/shared-storage-user/songdemin` |
| 用户工作目录 | `/mnt/shared-storage-user/songdemin/user/wuyinqi` |
| 用户指定的单机配置 | `-P 1 --gpu 8 --cpu 128 --memory 160000` |

这是本项目配置，不是集群通用要求。用户的新指示优先；不要默默更换 image、组、挂载位置或卡数。`memory` 单位为 MiB，是主机内存。原组合已在 H200 节点完成检查，日志包含 8 张 H200、`MOUNT_READ_WRITE_OK`、`SMOKE_OK`。基础镜像的系统 Python 没有 torch。历史成功不保证当前有空闲资源；共享 conda 环境和 DDP 训练仍需针对实际任务验证。

## 使用前

- 此 skill 文件夹可整体复制到其他项目；脚本和必要参考资料均位于文件夹内。使用时确认当前项目根目录，不依赖 skill 的安装层级；本地文件不一定存在于 rjob，运行代码需要放在共享存储或显式制作进镜像。分享给其他用户时，先按其实际权限更新本项目配置和脚本中的对应值。
- 确认实际 conda 前缀及训练入口。不要假设开发机的 `(base)` 环境会被继承，也不要把示例 `envs/train` 当成已存在的环境。优先调用环境的绝对 `bin/python` 或 `bin/torchrun`。
- 根据本次权限和错误判断。以前受限执行环境曾 DNS 解析失败、Docker socket 被拒绝、GPFS 显示只读；权限开放后 rjob 提交成功。不把历史限制当成平台故障，不自行关闭限制。

## 按任务读取

- 启动脚本、提交、状态查询、删除和失败诊断：读 [references/rjob-operations.md](references/rjob-operations.md)。
- 自动研究、训练迭代和结果比较：再读 [references/experiment-loop.md](references/experiment-loop.md)。
- 普通检查可复用 [scripts/submit_gpu_smoke.py](scripts/submit_gpu_smoke.py)，支持 `--dry-run`、`--output-dir` 和 `--run-dir`；在共享运行目录生成 `run.sh` 和 [gpu_smoke.sh](scripts/gpu_smoke.sh)，通过 `-- bash` 启动，检查最多约 130 秒（含强制退出宽限期）。提交记录默认写入调用时的工作目录。

## 实验 pipeline

每个实验必须按以下三步执行。任务运行产生的控制台 stdout/stderr 不会通过这套 pipeline 自动返回给 Codex；pipeline 走完后，实验分析只读取 mount 目录中的文件。`submit` 返回的任务名和 `get` 返回的状态是任务管理信息，不是训练程序的输出。必须在提交前落实日志和结果落盘，否则仅打印到控制台的指标无法用于后续分析。

### Step 1：写好 Bash 脚本，用 rjob submit 启动

在共享 mount 下创建独立 `RUN_DIR`，准备本轮源码快照和 `run.sh`。脚本明确设置真实 conda 环境、训练入口、输出目录和运行时间上限，使用 `set -euo pipefail` 保留真实退出码，训练进程在前台执行。

在脚本开头设置共享输出路径，并立即重定向整个脚本的 stdout/stderr，覆盖环境初始化、检查、训练以及 traceback：

```bash
#!/usr/bin/env bash
set -euo pipefail
# 提交前替换为本轮真实共享目录；本地变量不会自动传入 worker。
RUN_DIR="/mnt/shared-storage-user/songdemin/user/wuyinqi/outputs/<run-id>"
mkdir -p "$RUN_DIR"
exec > "$RUN_DIR/console.log" 2>&1
# 后续写入实际环境初始化、cd、带时间上限的训练命令。
# 程序还须将 metrics.json、图片和 checkpoint 显式保存到 RUN_DIR。
```

按 [rjob-operations.md](references/rjob-operations.md) 写完实际训练命令，先用 `bash -n` 检查脚本，再提交。以下 `RUN_ID`、`RUN_DIR` 使用本轮已确定的值：

```bash
set -euo pipefail
rjob submit \
  --name "$RUN_ID" \
  --namespace ailab-llmagent \
  --charged-group llmagent_gpu \
  --private-machine group \
  -P 1 --gpu 8 --cpu 128 --memory 160000 \
  --image registry.h.pjlab.org.cn/ailab/ml-base:22.04-pjlab-20251117 \
  --mount gpfs://gpfs1/songdemin:/mnt/shared-storage-user/songdemin \
  -- bash "$RUN_DIR/run.sh" 2>&1 | tee "$RUN_DIR/submit.log"
```

`--` 与 `bash` 之间有空格，不是 `--bash` 参数。记录提交输出 `created rjob_name: ...` 后的完整 metadata name 作为 `task_name`；不能用自定的 `--name` 或历史任务名代替。提交记录、状态记录和实验产物都保存到本轮 mount 目录。

### Step 2：用 rjob get task_name 查询状态

```bash
# task_name 来自本次 rjob submit 输出的 created rjob_name
rjob get "$task_name" --namespace ailab-llmagent
```

每隔 20～60 秒查询一次。排队、启动或运行状态继续等待，遵守本轮预算；任务总体状态显示 `Succeeded`（即 succeeded）时，表示执行成功完成。失败状态或查询报错不能当成完成成功，也不要因此重复提交。将最终状态和 task_name 记录到 mount 中，供删除后回溯。

### Step 3：看到 Succeeded 后，用 rjob delete task_name 删除

确认任务总体状态为 `Succeeded` 后，检查 mount 中的日志和结果已经保存，再删除本次任务：

```bash
rjob delete "$task_name" --namespace ailab-llmagent
```

删除任务完成资源清理，不删除共享 mount 文件。随后从 mount 读取 `console.log`、`metrics.json`、图片和 checkpoint，判断实验结果并决定下一轮改动。`Succeeded` 只表示程序执行成功，不表示指标改善；缺少产物时报告缺失，不能凭任务状态推测数值或控制台内容。

失败任务先读取 mount 中保存的错误和日志，再清理、修复或结束。需要额外诊断时将检查命令写入下一轮启动脚本，并将输出保存到 mount。用户要求中断或达到预算时，处理本次仍运行的任务并保留阶段文件。

## 核心执行约定

1. 区分创建成功、调度成功、程序成功和实验有效。保存 API 返回的完整 metadata name；`created rjob_name` 仅表示接受提交。`Succeeded` 需结合退出码、预期标记和结果文件判断。基础 smoke 成功不表示 torch 或 8 卡协同训练已验证。
2. GPFS 是同一份持久存储，不是每个容器的私有副本。写入、删除和覆盖会影响其他访问者。每个实验使用独立源码快照和输出目录，不修改已提交或正在运行实验的代码、依赖环境。
3. 所有远程执行均写入启动 Bash 脚本并作为 rjob 主命令提交。通过 `rjob get` 监控，通过 mount 中的日志和结果文件分析。需要额外检查时写入下一次任务脚本并落盘。
4. 在用户已授权的目标与预算内自主修改、运行、分析，不每轮重复确认。用有限实验计划约束探索；范围、资源或停止条件无法从请求推导时，先完成本地准备，再补齐必要信息。此 skill 本身不授权额外实验、镜像推送或扩大预算。
5. 只管理本次创建的任务，删除或停止操作使用完整 metadata name。提交结果不确定时先查任务，避免重复提交。确定性失败先修复原因；相同条件的瞬时失败最多重试一次，再报告证据或等待外部条件改变。
6. 报告代码变化、配置、任务 ID、指标、产物路径和局限。会话结束不能声称持续后台监控；未完成任务留下可继续查询的 ID 和状态。删除已完成且已保存结果的自建任务，保留共享结果。

## 可选原始资料

必要操作已整理在本 skill 的 references 中，运行脚本不依赖原项目的 PDF。原项目 `rjob&rlaunch/` 中的 `013--rjob分布式训练任务.pdf`、`012--rlaunch.pdf` 和 `011--镜像仓库问题.pdf` 是整理依据；若使用者另有这些文件，仅在相关行为不明确或请求制作镜像时读取。CLI 参数以本机 `rjob submit --help` 等输出为准，文档中的其他用户、组和镜像不是本项目默认值。
