# rjob 启动脚本、查询与删除

禁止使用 SSH 连接服务器或 worker，不启用 sshd，不创建保活任务。远程命令全部写入共享存储的 Bash 启动脚本，再提交任务执行；通过 rjob 查询和共享文件获取结果。

## 写启动脚本并提交

使用 SKILL.md 的项目配置。先落实真实 `ENV_PREFIX` 和本轮源码快照 `SOURCE_DIR`，以下以 `train.py` 为入口示例，按实际训练参数调整。启动脚本和源码都必须在 worker 可访问的共享存储中。

```bash
: "${ENV_PREFIX:?先设置真实 conda 环境绝对路径}"
: "${SOURCE_DIR:?先设置本轮共享源码快照绝对路径}"
RUN_ID="wuyinqi-exp-$(date +%Y%m%d-%H%M%S)-${RANDOM}"
RUN_DIR="/mnt/shared-storage-user/songdemin/user/wuyinqi/outputs/$RUN_ID"
mkdir -p "$RUN_DIR"

# 将本地已确定的路径写入脚本，不依赖 worker 继承本地变量。
{
  printf '#!/usr/bin/env bash\nset -euo pipefail\n'
  printf 'ENV_PREFIX=%q\nSOURCE_DIR=%q\nRUN_DIR=%q\n' "$ENV_PREFIX" "$SOURCE_DIR" "$RUN_DIR"
  cat <<'RUN'
exec > "$RUN_DIR/console.log" 2>&1
cd "$SOURCE_DIR"
# 示例运行上限：590 秒加 10 秒强制退出宽限期；按授权预算调整。
timeout --signal=TERM --kill-after=10s 590s \
  "$ENV_PREFIX/bin/python" -u train.py --output-dir "$RUN_DIR" \
  2>&1 | tee "$RUN_DIR/train.log"
RUN
} > "$RUN_DIR/run.sh"
bash -n "$RUN_DIR/run.sh"

rjob submit \
  --name "$RUN_ID" \
  --namespace ailab-llmagent \
  --charged-group llmagent_gpu \
  --private-machine group \
  -P 1 --gpu 8 --cpu 128 --memory 160000 \
  --image registry.h.pjlab.org.cn/ailab/ml-base:22.04-pjlab-20251117 \
  --mount gpfs://gpfs1/songdemin:/mnt/shared-storage-user/songdemin \
  -- bash "$RUN_DIR/run.sh"
```

正确语法为 `-- bash`，不是 `--bash` 参数。本机 rjob 源码规定 `submit [options] -- <command line>`。

使用 `set -euo pipefail`，防止 tee 成功掩盖训练失败。训练主进程在前台运行，不用 `nohup`、后台 `&` 或 sleep 保活，以便平台状态反映脚本退出结果。日志不打印 AK/SK、私钥或其他凭据。

共享 conda 前缀应保持开发机与 worker 路径一致。直接调用真实环境的绝对 Python；依赖 activate 时在脚本中加载真实 conda 初始化脚本并激活。安装依赖前确认环境无人使用，或另建版本化环境。不修改已提交任务的启动脚本、源码或环境。

## 现有 smoke 脚本

将 `SKILL_DIR` 设置为本 skill 的实际绝对路径，从项目工作目录运行：

```bash
python3 "$SKILL_DIR/scripts/submit_gpu_smoke.py" --dry-run
python3 "$SKILL_DIR/scripts/submit_gpu_smoke.py" --output-dir "$RUN_DIR/submission"
```

第二条仅在授权包含实际检查时执行。脚本默认在共享用户目录的 `outputs/<唯一任务名>/` 下生成 `run.sh` 和 `gpu_smoke.sh`，通过 `-- bash` 提交；`--run-dir` 可指定新的共享运行目录，目录已存在则拒绝覆盖。`--dry-run` 不创建目录、不写记录、不提交任务。

默认内存为 160000 MiB，可用 `--memory` 覆盖。提交记录 `gpu-smoke-job.json`、`gpu-smoke-submit-command.txt`、`gpu-smoke-submit.log` 默认写入当前工作目录；用 `--output-dir` 保存到独立目录。不要将运行产物放入 skill 文件夹。读取 JSON 的 `metadata_name` 进行查询，不能只依赖可重复的 showname。脚本只负责提交，不自动查询或删除；Codex 随后执行下面的流程。

实验结果只从 mount 文件读取：此 submit → get → delete pipeline 不自动返回训练 stdout/stderr。必须在启动脚本中保存整个脚本的日志，并让程序显式写入指标、图片和 checkpoint。

## rjob get 判断完成

`JOB` 必须是本次提交返回的完整 metadata name：

```bash
rjob get "$JOB" --namespace ailab-llmagent
```

通常每隔 20～60 秒查询一次，直到任务总体状态显示 `Succeeded`。此状态表示主程序成功退出，随后核对 mount 中的日志和结果；不保证实验指标改善。不要仅匹配某个 replica 的 succeeded 计数而忽略任务总体状态。

排队、启动、运行状态继续等待，遵守预算；失败状态先读取 mount 日志、保存状态后修复或结束。查询失败或客户端断开不代表任务完成，也不应因此重复提交。

```bash
rjob events "$JOB" --namespace ailab-llmagent
```

## rjob delete 清理

确认 `Succeeded` 后，检查 mount 中的日志、指标、JSON 和图片已保存，并记录最终状态，然后删除任务：

```bash
rjob delete "$JOB" --namespace ailab-llmagent
```

只删除本次创建且已保存结果的任务。正常完成后计算进程通常已退出并释放资源，delete 清理任务对象；GPFS 文件仍保留。失败任务先保存证据再删除。用户要求中断或运行超出预算时可使用 `rjob stop` 处理本次仍运行的任务，随后保存证据并删除，不误判为成功。

## 诊断和多卡

- DNS 或连接错误：检查当前执行环境和平台连通性；查询错误不能视为任务不存在。
- 403 或配额错误：核对 namespace、组、镜像及存储权限。
- Pending / Inqueue：查看 events，不擅自降低卡数或重复提交。
- 镜像拉取失败：核对完整 tag 和权限，不静默换 image。
- OOMKilled：检查主机内存限制，与 CUDA 显存不足分开处理。
- 程序失败：保存 traceback 和退出码，修改下一轮代码或脚本后重新提交。
- `Succeeded`：核对输出文件完整、可解析以及指标有效；基础 smoke 不验证 DDP。

启动阶段 `configmap not found`、gang 尚未 ready 或辅助 vscode/jupyter 退出不能单独判为失败，结合最终状态和主程序日志判断。`CUDA_VISIBLE_DEVICES` 未设置不能单独判为 GPU 不可用。

`-P 1 --gpu 8` 为一个副本申请 8 卡；协同训练还需真实环境的 `torchrun --standalone --nnodes=1 --nproc-per-node=8` 和 DDP 程序，将其写入 run.sh 替换单进程示例。单独 `python train.py` 不自动用满 8 卡。

多副本不自动成为分布式训练。多机时再查平台文档的 `DISTRIBUTED_JOB=true`、`NODE_COUNT`、`NODE_RANK`、`MASTER_ADDR`、`PROC_PER_NODE` 及 RDMA，保留平台注入的网络变量。单机不无依据添加多机/RDMA 参数。
